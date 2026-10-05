# Recording the STT test set (about 10 minutes)

The Day 3 comparison of Deepgram Nova-3 against Flux, and the replay harness, need real recordings of your voice saying typical caller lines. You record once; both recognisers then run on the same audio.

## Steps

1. In `.env`, set `DEV_CAPTURE_AUDIO=true` and restart the server.
2. Put on the headset you'll use for the demo, in the room you'll demo in if possible.
3. Open http://localhost:8000/?mode=listen and click the circle. Emma stays silent in this mode: she transcribes and records, but doesn't reply.
4. Read the 30 lines in [tests/data/stt_script.json](../tests/data/stt_script.json) (they're repeated below) in order, at a natural pace, pausing about 2 seconds between lines. Don't correct yourself; if you stumble, just carry on.
5. Click the circle to hang up. The recording is in `captures/`: a `.wav` of your audio and a `.jsonl` of what Deepgram heard.
6. Set `DEV_CAPTURE_AUDIO=false` again.

Optional: record a second pass with a different speaker (a friend or family member) or on speaker instead of a headset. That's a harder test and makes the comparison more honest.

The files contain your voice. They stay in `captures/`, which git ignores; delete them whenever you like.

## The lines

1. Hi, I'd like to book an appointment please.
2. My name is Adharsh Kumar.
3. It's nine eight seven six five, four three two one zero.
4. Double nine, eight seven six, five four three, two one.
5. Yes, that's correct.
6. No, that's wrong.
7. I need a root canal.
8. Just a cleaning and check-up for my daughter.
9. Can I get Invisalign at the Indiranagar branch?
10. Whitefield is closer for me.
11. Jayanagar, with a lady doctor if possible.
12. Next Monday around five.
13. Tomorrow at ten thirty in the morning.
14. The twenty sixth, in the evening.
15. Sometime next week, after five.
16. Half past four on Friday.
17. Whatever is earliest.
18. Seven.
19. Seven in the evening.
20. Actually, make it Tuesday instead.
21. The second one works.
22. What time do you close on Saturdays?
23. Where is your Jayanagar clinic?
24. Sorry, can you repeat that?
25. Hold on a second.
26. I want to cancel my appointment on the fifth.
27. Can I move my cleaning to Thursday afternoon?
28. I have really bad swelling and pain since last night.
29. Can I talk to a real person?
30. No, that's all, thank you. Bye.
