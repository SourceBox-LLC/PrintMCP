# Tutorial 1 · Find Something to Print

> **Goal:** get a 3D model onto your computer just by asking — no websites to dig through, no
> files to wrangle by hand.
> **Time:** ~5 minutes · **You need:** PrintMCP connected to your AI assistant
> ([Getting Started](../getting-started.md)).

You don't operate PrintMCP directly. You **talk to your AI assistant** — Claude, or whatever
client you've connected — and it does the searching and downloading for you. This tutorial shows
you what to say and what to expect back.

We'll find a **coffee cup** to print, and keep using it through the next tutorials.

---

## Just ask

Open a chat with your assistant and say what you want in plain English. For example:

> 💬 **You:** "I'd like to 3D print a coffee cup. Can you find a few options on Thingiverse?"

Your assistant will go search and come back with a short list, something like:

> 🤖 **Assistant:** "I found a few coffee cups on Thingiverse:
> 1. **Coffee Cup** by Barspin — a simple classic mug (42 likes)
> 2. **Espresso Cup** by maker_jane — small, no handle (18 likes)
> 3. **Travel Mug** by printsmith — tall with a lid (96 likes)
>
> Want details on any of these, or should I look for something more specific?"

That's it — no browsing, no copying links. You described what you wanted and got real options.

> [!TIP]
> **Be as specific as you like.** "A coffee cup with a big handle," "an espresso cup without a
> handle," or "a travel mug with a screw-on lid" all work. The more detail you give, the better
> the matches. If nothing looks right, just say *"none of these — show me a few more"* or
> *"something simpler."*

---

## Check before you commit

Before downloading, it's worth knowing two things: **is it actually printable**, and **are you
allowed to use it the way you intend?** Just ask:

> 💬 **You:** "Tell me more about the first one. What's its license — can I sell prints of it?"

> 🤖 **Assistant:** "**Coffee Cup** by Barspin is licensed *Creative Commons – Attribution*,
> which means you can print, modify, and even sell it as long as you credit the creator. It comes
> as two STL files (the cup, and a no-support variant). Want me to download it?"

> [!IMPORTANT]
> **Licenses matter.** Many models are free to print at home but *not* to sell, or require credit.
> Your assistant surfaces the license precisely so you don't accidentally misuse someone's work.
> If you're printing just for yourself, almost anything is fine — but it's good to know.

---

## Download it

When you're happy, just say so:

> 💬 **You:** "Perfect, download it."

> 🤖 **Assistant:** "Done! I've downloaded 2 files for the Coffee Cup to your computer:
> - `Coffee_Cup.A.1.stl`
> - `Coffee_Cup_By_Barspin_WO_Support_Piece.stl`
>
> They're saved in your PrintMCP downloads folder. Want me to slice one so it's ready to print?"

### Where do the files go?

Onto **your computer**, in a `PrintMCP/downloads` folder inside your home directory (each model
gets its own subfolder). You don't need to manage this — your assistant remembers where things
are and uses them in the next step. If you ever want them somewhere specific, you can say
*"save downloads to my D: drive"* once and set it up
([details here](../configuration.md#-where-print-files-are-stored)).

---

## Tips for better results

- **Talk like you would to a person.** "Find me something to organize my desk" works as well as
  a precise model name.
- **Iterate.** Don't love the options? *"Show me more,"* *"something with cleaner lines,"* or
  *"something a beginner can print"* will refine the search.
- **Ask about the practical stuff.** *"Which of these would be easiest to print?"* or *"how big
  is that one?"* — the assistant can reason about the files it found.
- **You don't have to download everything.** If a model has many files, say *"just grab the main
  cup file."*

---

## ✅ You've done it

You found a real model and saved it to your computer — entirely by chatting. 

**Next:** [Tutorial 2 · Get It Print-Ready](02-slice-for-your-printer.md) — turn that model into
something your printer understands.

---

<sub>Curious what's happening behind the scenes? Your assistant is using PrintMCP's
[Thingiverse tools](../tools/thingiverse.md) — but you never have to touch them directly.</sub>
