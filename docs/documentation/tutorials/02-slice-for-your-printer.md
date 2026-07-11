# Tutorial 2 · Get It Print-Ready

> **Goal:** turn the model you found into a file your printer can actually run — and understand
> what you're choosing along the way.
> **Time:** ~5 minutes · **You need:** Ultimaker Cura installed ([Getting Started](../getting-started.md))
> and a model from [Tutorial 1](01-find-and-download.md).

A 3D model is just a *shape*. Your printer needs *instructions* — exactly where to move, how
fast, how hot, layer by layer. Turning a shape into instructions is called **slicing**, and your
assistant does it for you. You just describe how you want it printed.

This step happens entirely on your computer — no printer needed yet.

We'll continue with the coffee cup from Tutorial 1.

---

## Just ask

If you downloaded a model in the last tutorial, your assistant already knows where it is. Simply
say:

> 💬 **You:** "Slice the coffee cup so it's ready to print."

> 🤖 **Assistant:** "Sliced! Here's the summary for your **Coffee Cup**:
> - **Estimated print time:** about 6 hours 30 minutes
> - **Filament needed:** ~25.5 meters
> - Set up for a Creality Ender-3 Pro at standard quality (0.2 mm layers, 20% infill)
>
> The print file is saved next to your model. Want to send it to your printer, or adjust anything
> first?"

Notice what you got: not just a file, but the **real cost** — how long it'll take and how much
plastic it'll use. That's your chance to sanity-check before committing hours of printing.

> [!TIP]
> If your assistant doesn't know which printer you have, just tell it once: *"I have a Creality
> Ender-3 Pro"* (or whatever you own). It'll remember for the rest of the conversation.

---

## Describe how you want it printed

You don't need to know slicer jargon. Describe the *outcome* you want, and the assistant
translates it into the right settings:

| What you say | What it does |
|--------------|--------------|
| 💬 *"Make it faster, I don't need it to be pretty"* | Thicker layers, less infill — quicker, less plastic |
| 💬 *"I want it really smooth and detailed"* | Thinner layers — finer finish, longer print |
| 💬 *"Make it strong, it needs to hold weight"* | More infill — sturdier, more plastic |
| 💬 *"It has some overhangs — add supports"* | Generates removable support structures |
| 💬 *"It keeps warping off the bed"* | Adds a brim or raft for better adhesion |
| 💬 *"I'm printing in PETG, not PLA"* | Adjusts the temperatures for your material |

For example:

> 💬 **You:** "Actually, can you make it print faster? It's just a test."

> 🤖 **Assistant:** "Re-sliced at draft quality (0.3 mm layers, 10% infill). That brings it down
> to about **4 hours 40 minutes** and uses less filament. Ready when you are."

You changed the outcome with one sentence — no menus, no numbers to look up.

---

## Use the estimate to make a decision

The time and filament estimate isn't just trivia — it's how you catch problems *before* wasting
6 hours and a spool of plastic:

- **Too long?** Ask to *"make it faster"* or *"print it smaller."*
- **More filament than you have on the spool?** Now you know to swap it first.
- **Wildly off from what you expected?** Something's probably wrong (wrong size or settings) —
  ask the assistant about it.

> 💬 **You:** "Will that fit on a 220mm bed? And do I have enough filament if my spool's about
> half full?"

Your assistant can reason about these from the model and the estimate, and warn you if something
won't work.

---

## Where the print file goes

It's saved **right next to the original model** on your computer, as a `.gcode` file. You don't
need to track it — your assistant handles handing it to the printer in the next tutorial. (If you
don't have an OctoPrint-connected printer, this `.gcode` file is exactly what you'd copy onto an
SD card to print the traditional way.)

---

## ✅ You've done it

You turned a downloaded shape into a real, print-ready file — and you know what it'll cost in
time and plastic. 

**Next:** [Tutorial 3 · Send It to Your Printer](03-print-with-octoprint.md) — start the actual
print and watch it go.

---

<sub>Behind the scenes your assistant uses PrintMCP's [Cura slicing tool](../tools/cura.md) —
but you only ever describe what you want.</sub>
