# Tutorial 3 · Send It to Your Printer

> **Goal:** start the actual print — and keep an eye on it — just by asking.
> **Time:** ~5 minutes to set up (then however long the print takes) · **You need:** a printer
> running OctoPrint, set up in [Getting Started](../getting-started.md), and a print-ready file
> from [Tutorial 2](02-slice-for-your-printer.md).

This is the exciting part: turning a file into a real object. Because this controls a **physical
machine** — one with hot parts and moving motors — your assistant has a built-in safety habit
you'll see in action here.

> [!WARNING]
> **The safety rule, in one sentence:** before your assistant does anything that physically moves
> the printer or heats it up, it tells you what it's about to do and waits for you to confirm.
> Nothing happens to your machine by surprise. You'll see this below.

---

## Step 1 — Make sure the printer's ready

Start by just asking how things look:

> 💬 **You:** "Is my printer ready to print?"

> 🤖 **Assistant:** "Yes — your printer is connected and idle, nozzle and bed are at room
> temperature. Ready to go. Want me to start the coffee cup?"

If instead it says something like *"I can't reach your printer,"* the printer is probably off, on
a different network, or its address changed. The [Troubleshooting guide](../troubleshooting.md)
has a quick checklist. If it says the printer is reachable but *not connected*, just say
*"connect it"* and your assistant will bring it online (confirming with you first).

---

## Step 2 — Start the print

Just ask:

> 💬 **You:** "Great, start printing the coffee cup."

Here's the safety habit in action. Rather than silently firing up the machine, your assistant
pauses to confirm:

> 🤖 **Assistant:** "Just to confirm before I start the physical printer: I'll upload
> **Coffee_Cup.A.1.gcode** and begin printing it on your Ender-3 Pro. This will heat the nozzle
> to ~200°C and start moving. Shall I go ahead?"

> 💬 **You:** "Yes, go."

> 🤖 **Assistant:** "Printing has started! 🎉 The printer is heating up now and will begin laying
> down the first layer shortly. I'll check progress whenever you ask."

> [!NOTE]
> **Why the extra confirmation?** Starting a print, heating the nozzle, or moving the head are
> real-world actions you can't fully undo. The pause is a deliberate safety net — especially handy
> when an assistant is doing the work. You stay in control of anything physical.

---

## Step 3 — Watch it go

Check in any time, in plain language:

> 💬 **You:** "How's the print coming along?"

> 🤖 **Assistant:** "It's about **42% done** — roughly 2 hours 45 minutes in, with around 3 hours
> 45 minutes left. Everything looks healthy."

Ask as often as you like — checking status never affects the print.

---

## Step 4 — If something goes wrong

Spot a problem — a knocked-loose part, a tangle, bad first layer? Just say so:

> 💬 **You:** "Something looks off, pause it."

> 🤖 **Assistant:** "Paused. The printer's holding in place and staying warm. Say the word to
> resume, or I can cancel it entirely."

Your options, in plain English:
- 💬 *"Resume"* — pick up where it left off.
- 💬 *"Cancel it"* — stop for good (your assistant will confirm first, since the half-finished
  print can't be recovered).

> [!WARNING]
> **Cancelling can't be undone** — the partially printed object is wasted. That's why your
> assistant always double-checks before cancelling.

---

## Other things you can just ask for

You're not limited to a fixed sequence. Natural requests work for setup and maintenance too:

- 💬 *"Preheat the nozzle and bed for PLA so it's ready when I get back."*
- 💬 *"Home the printer."*
- 💬 *"Raise the nozzle 10mm so I can clear the bed."*
- 💬 *"Cool everything down."*

Each of these moves or heats the machine, so your assistant will confirm before acting — same
safety habit, every time.

---

## ✅ You've done it

You started a real print, watched its progress, and learned how to pause or stop it — all by
chatting, with a safety check on anything physical. 

**Next:** [Tutorial 4 · From Idea to Object, Start to Finish](04-end-to-end.md) — put the whole
thing together in a single conversation.

---

<sub>Want the deeper reasoning behind the confirmation step? See the [Safety guide](../safety.md).
The underlying [printer tools](../tools/octoprint.md) are documented too — but you only ever talk
to your assistant.</sub>
