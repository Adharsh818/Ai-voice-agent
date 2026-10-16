"""
Get Emma's Oracle Always Free server (docs/DEPLOY.md, RESUME_HERE task 9).

    %USERPROFILE%\\oci-venv\\Scripts\\python.exe tools\\oracle_launch.py --check
    %USERPROFILE%\\oci-venv\\Scripts\\python.exe tools\\oracle_launch.py

Free Ampere (A1) servers in Hyderabad are often "out of host capacity"; a slot
frees up for a few seconds at random. This script keeps asking, slowly enough
that Oracle doesn't answer "too many requests", and stops the moment one
server exists.

Needs Oracle's Python SDK in its own venv (`pip install oci`, not Emma's
.venv) and an API key in %USERPROFILE%\\.oci\\config. It only ever creates
Always Free things: one VM.Standard.A1.Flex inside the 4 OCPU / 24 GB limit, a
network with a public subnet, and ingress for SSH, HTTP and HTTPS. It never
creates a second server: if one named --name already exists it reports it
and stops.

--check     read-only: sign in, show the image and network it would use
(default)   set up the network if needed, then try to launch until it works
"""

import argparse
import os
import sys
import time
from datetime import datetime

import oci

SHAPE = "VM.Standard.A1.Flex"
FREE_OCPUS, FREE_MEMORY_GB = 4, 24             # Always Free A1 limit for the whole tenancy
WEB_PORTS = (22, 80, 443)
VCN_CIDR, SUBNET_CIDR = "10.0.0.0/16", "10.0.0.0/24"
SSH_KEY = os.path.expanduser("~/.ssh/emma_oracle.pub")
RESULT_FILE = os.path.expanduser("~/.oci/emma_server.txt")


def log(msg):
    print(f"{datetime.now():%d %b %H:%M:%S}  {msg}", flush=True)


def parse_sizes(text):
    sizes = []
    for part in text.split(","):
        ocpus, mem = (float(x) for x in part.split(":"))
        if ocpus > FREE_OCPUS or mem > FREE_MEMORY_GB:
            sys.exit(f"{part}: over the Always Free limit ({FREE_OCPUS} OCPU / {FREE_MEMORY_GB} GB)")
        sizes.append((ocpus, mem))
    return sizes


# ------------------------------------------------------------------ lookups
def ubuntu_image(compute, compartment):
    images = compute.list_images(compartment, operating_system="Canonical Ubuntu",
                                 operating_system_version="24.04", shape=SHAPE,
                                 sort_by="TIMECREATED", sort_order="DESC").data
    plain = [i for i in images if "Minimal" not in i.display_name]
    if not plain:
        sys.exit("No Canonical Ubuntu 24.04 image for the A1 shape in this region.")
    return plain[0]


def existing_server(compute, compartment, name):
    for inst in compute.list_instances(compartment, display_name=name).data:
        if inst.lifecycle_state not in ("TERMINATED", "TERMINATING"):
            return inst
    return None


def public_subnet(net, compartment):
    """A subnet that allows public IPs, in any VCN of the compartment (the console's failed tries may have made one)."""
    for vcn in net.list_vcns(compartment).data:
        if vcn.lifecycle_state != "AVAILABLE":
            continue
        for sub in net.list_subnets(compartment, vcn_id=vcn.id).data:
            if sub.lifecycle_state == "AVAILABLE" and not sub.prohibit_public_ip_on_vnic:
                return vcn, sub
    return None, None


# ------------------------------------------------------------------ network
def ensure_internet_route(net, compartment, vcn):
    gws = [g for g in net.list_internet_gateways(compartment, vcn_id=vcn.id).data
           if g.lifecycle_state == "AVAILABLE"]
    if gws:
        gw = gws[0]
    else:
        log("creating the internet gateway")
        gw = net.create_internet_gateway(oci.core.models.CreateInternetGatewayDetails(
            compartment_id=compartment, vcn_id=vcn.id, is_enabled=True, display_name="emma-igw")).data
        oci.wait_until(net, net.get_internet_gateway(gw.id), "lifecycle_state", "AVAILABLE")
    rt = net.get_route_table(vcn.default_route_table_id).data
    if not any(r.destination == "0.0.0.0/0" for r in rt.route_rules):
        log("adding the default route to the internet")
        rules = rt.route_rules + [oci.core.models.RouteRule(
            destination="0.0.0.0/0", destination_type="CIDR_BLOCK", network_entity_id=gw.id)]
        net.update_route_table(rt.id, oci.core.models.UpdateRouteTableDetails(route_rules=rules))


def ensure_ports(net, subnet):
    for sl_id in subnet.security_list_ids:
        sl = net.get_security_list(sl_id).data
        have = {r.tcp_options.destination_port_range.min for r in sl.ingress_security_rules
                if r.protocol == "6" and r.source == "0.0.0.0/0" and r.tcp_options
                and r.tcp_options.destination_port_range}
        missing = [p for p in WEB_PORTS if p not in have]
        if missing:
            log(f"opening TCP {', '.join(map(str, missing))} in {sl.display_name}")
            rules = sl.ingress_security_rules + [oci.core.models.IngressSecurityRule(
                protocol="6", source="0.0.0.0/0", source_type="CIDR_BLOCK",
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(min=p, max=p)))
                for p in missing]
            net.update_security_list(sl.id, oci.core.models.UpdateSecurityListDetails(
                ingress_security_rules=rules))
        return                                           # the first list is the subnet's default one


def ensure_network(net, compartment):
    vcn, sub = public_subnet(net, compartment)
    if sub is None:
        log("creating the network (VCN + public subnet)")
        vcn = net.create_vcn(oci.core.models.CreateVcnDetails(
            compartment_id=compartment, cidr_blocks=[VCN_CIDR], display_name="emma-vcn",
            dns_label="emmavcn")).data
        vcn = oci.wait_until(net, net.get_vcn(vcn.id), "lifecycle_state", "AVAILABLE").data
        sub = net.create_subnet(oci.core.models.CreateSubnetDetails(
            compartment_id=compartment, vcn_id=vcn.id, cidr_block=SUBNET_CIDR,
            display_name="emma-subnet", dns_label="emmasub", prohibit_public_ip_on_vnic=False)).data
        sub = oci.wait_until(net, net.get_subnet(sub.id), "lifecycle_state", "AVAILABLE").data
    ensure_internet_route(net, compartment, vcn)
    ensure_ports(net, sub)
    return vcn, sub


# ------------------------------------------------------------------ launch
def public_ip(compute, net, compartment, inst):
    for att in compute.list_vnic_attachments(compartment, instance_id=inst.id).data:
        ip = net.get_vnic(att.vnic_id).data.public_ip
        if ip:
            return ip
    return None


def launch(compute, compartment, ad, name, image, subnet, ocpus, mem, ssh_key):
    return compute.launch_instance(oci.core.models.LaunchInstanceDetails(
        availability_domain=ad, compartment_id=compartment, display_name=name, shape=SHAPE,
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=ocpus, memory_in_gbs=mem),
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=image.id),
        create_vnic_details=oci.core.models.CreateVnicDetails(subnet_id=subnet.id, assign_public_ip=True),
        metadata={"ssh_authorized_keys": ssh_key})).data


def report(compute, net, compartment, inst):
    log(f"server '{inst.display_name}' is {inst.lifecycle_state}; waiting for RUNNING")
    inst = oci.wait_until(compute, compute.get_instance(inst.id), "lifecycle_state", "RUNNING",
                          max_wait_seconds=900).data
    ip = public_ip(compute, net, compartment, inst)
    shape = f"{inst.shape_config.ocpus:g} OCPU / {inst.shape_config.memory_in_gbs:g} GB"
    text = f"name={inst.display_name}\nshape={shape}\npublic_ip={ip}\nid={inst.id}\n"
    with open(RESULT_FILE, "w", newline="\n") as f:
        f.write(text)
    print("\n" + "=" * 60 + f"\n  SERVER READY: {shape}, public IP {ip}\n  (saved in {RESULT_FILE})\n" + "=" * 60,
          flush=True)
    print("\a", end="", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="read-only: sign in and show the plan")
    ap.add_argument("--name", default="emma")
    ap.add_argument("--sizes", default="2:12,1:6", help="OCPU:GB, one per try, taking turns")
    ap.add_argument("--interval", type=int, default=240, help="seconds between tries")
    args = ap.parse_args()
    sizes = parse_sizes(args.sizes)

    config = oci.config.from_file()
    compartment = config["tenancy"]
    identity = oci.identity.IdentityClient(config)
    compute = oci.core.ComputeClient(config)
    net = oci.core.VirtualNetworkClient(config)

    user = identity.get_user(config["user"]).data
    ads = [ad.name for ad in identity.list_availability_domains(compartment).data]
    image = ubuntu_image(compute, compartment)
    log(f"signed in as {user.name}, region {config['region']}, availability domains {ads}")
    log(f"image {image.display_name}")

    inst = existing_server(compute, compartment, args.name)
    if inst:
        log(f"a server named '{args.name}' already exists ({inst.lifecycle_state}); not creating another")
        if not args.check:
            report(compute, net, compartment, inst)
        return

    if args.check:
        vcn, sub = public_subnet(net, compartment)
        log(f"network: {'VCN ' + vcn.display_name + ', subnet ' + sub.display_name if sub else 'none yet (would create emma-vcn)'}")
        log(f"would try {' then '.join(f'{o:g} OCPU/{m:g} GB' for o, m in sizes)} in turn, one try every {args.interval} s")
        return

    with open(SSH_KEY) as f:
        ssh_key = f.read().strip()
    vcn, sub = ensure_network(net, compartment)
    log(f"network ready: VCN {vcn.display_name}, subnet {sub.display_name}")

    # One launch call per round: Oracle answers a second call seconds later with 429 (16 Oct),
    # so the sizes take turns and the availability domains take turns.
    wait, attempt = args.interval, 0
    while True:
        attempt += 1
        ocpus, mem = sizes[(attempt - 1) % len(sizes)]
        ad = ads[(attempt - 1) // len(sizes) % len(ads)]
        try:
            inst = launch(compute, compartment, ad, args.name, image, sub, ocpus, mem, ssh_key)
            log(f"try {attempt}: {ocpus:g} OCPU/{mem:g} GB accepted")
            report(compute, net, compartment, inst)
            return
        except oci.exceptions.ServiceError as e:
            if e.status == 500 and "capacity" in (e.message or "").lower():
                wait = args.interval
                log(f"try {attempt}: {ocpus:g} OCPU/{mem:g} GB out of capacity; next try in {wait} s")
            elif e.status == 429:
                wait = min(wait * 2, 900)
                log(f"try {attempt}: too many requests; next try in {wait} s")
            elif e.status in (400, 404) or e.code == "LimitExceeded":
                sys.exit(f"stopping: {e.status} {e.code}: {e.message}")
            else:
                log(f"try {attempt}: {e.status} {e.code}: {e.message}")
        if existing_server(compute, compartment, args.name):        # a launch that errored but went through
            report(compute, net, compartment, existing_server(compute, compartment, args.name))
            return
        time.sleep(wait)


def stay_awake():
    """Ask Windows not to idle-sleep while this runs (like a video player; no setting changes, ends with the process)."""
    if os.name == "nt":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


if __name__ == "__main__":
    stay_awake()
    # A new API key reaches Oracle's services one by one: for a few minutes some calls
    # answer 401 (16 Oct). Those, 429 and 5xx outside a launch are waited out and retried.
    while True:
        try:
            main()
            break
        except KeyboardInterrupt:
            log("stopped")
            break
        except oci.exceptions.ServiceError as e:
            if e.status not in (401, 429) and e.status < 500:
                raise
            log(f"{e.status} {e.code} from {e.operation_name}; starting again in 120 s")
            time.sleep(120)
        except oci.exceptions.RequestException as e:          # network dropped (hotspot)
            log(f"network error ({type(e).__name__}); starting again in 120 s")
            time.sleep(120)
