# NOVEL-TO-SCRIPT CONVERSION SYSTEM v4

Built for converting a full novel (up to 5 chapters at a time) into a TTS-ready YouTube narration script. **First-person MC POV. Texture mandatory.** Adapted from the original 3-prompt manhwa recap system, re-engineered for novel source material, with a continuity mechanism for multi-chunk processing, a deferred hook mechanism that lets the opening line be written with knowledge of the whole part, and a texture enforcement layer modeled on successful recap channels.

---

## HOW TO USE THIS

**Per chunk, paste ALL of the following in ONE message — no waiting between pieces:**

1. Prompt 1 (Universal Narration Engine) — always
2. Prompt 2 (the Niche Module that fits — A/B/C/D) — always
3. Prompt 3 (Opening Margin Layer) — **only** on the very first chunk of Part 1 of a brand-new novel. Never again after that, not even at the start of Part 2, 3, etc.
4. Prompt 4 (Chunk Continuation Layer) — on every chunk **except** the very first one
5. Your Continuity Tracker (filled in with everything established so far) — on every chunk except the very first one
6. The chapter text for this chunk (5 chapters maximum, ~7–12k words)

The AI processes immediately — nothing here waits for a follow-up message.

**After each chunk comes back, run these passes in order:**

1. **Self-audit pass** (Prompt 6) — list omitted plot points, invented details, repeated comparisons, tone drift. Does not rewrite.
2. **Fact-check pass** (Prompt 7) — cross-reference against source, verify every major plot point appears.
3. **Texture repair pass** (Prompt 8) — only if the output reads flat.
4. **TTS normalization pass** (Prompt 9) — numbers to words, acronyms expanded, homographs disambiguated, punctuation cleaned.
5. **Skim and fix** — read aloud at 2x, catch what the automated passes missed.
6. **Update the Continuity Tracker** with what that chunk introduced (Prompt 10 — Tracker Extraction, reviewed by the operator before it is merged).
7. **Extract scene metadata** (Prompt 11 — Metadata Extraction) for the parallel metadata file.

On chunk 1 specifically, check the margin against Prompt 3's self-check — if it reads hook-flavored (a rhetorical question, "little did he know" phrasing, dramatic word choice), run Prompt 3-REPAIR immediately to fix just the margin before continuing.

**Once every chunk in Part 1 is finished** (all 3–5 chunks assembled into one continuous script):

7. Run Prompt 5 (Deferred Hook Generation) **once** — this replaces chunk 1's placeholder margin with the real opening hook, now that the AI can see where the whole part actually goes. This happens exactly once per novel, only after Part 1 completes. It is never re-run for Part 2, 3, etc.
8. Splice the output into chunk 1 in place of the placeholder margin. The rest of chunk 1, and every other chunk, stays untouched.

---

## POV RULE (NON-NEGOTIABLE)

**The narration is first-person from the MC's point of view. Always. Without exception.**

- The MC is "I / me / my." Never "our guy," "my guy," "bro," or the MC's name in narration.
- The MC's name appears only when another character says it in dialogue.
- If the source has other POVs, convert them to what the MC can observe, infer, or hear. Do not switch into another character's head.
- This rule carries across every chunk, every part, and every module.

---

## THE TEXTURE RULE (NON-NEGOTIABLE)

**Texture is the MC's attitude toward the facts, not additional facts.** It is what separates a flat summary from a script that sounds like a person talking.

Every 2–4 sentences, the MC must have a small, dry, first-person aside — a reaction, a judgment, a framing — that shows personality. Never explained. Never a punchline. Never forced. If a passage is just flat facts, add a reaction without adding new plot. The MC has opinions. Let them show.

**Failed example (flat, no texture):**
> Reborn into a family of peasants, they immediately tell him the kid has zero magical talent. To make matters worse, their house is that classic rundown shack so poor it's falling apart. As the third of four siblings, our little MC spends his days with his stomach rumbling with hunger.

**Successful example (same facts, textured):**
> So I got reborn into a family of peasants. They took one look at me and declared I had zero magical talent. Classic. Our house was a shack held together by hope and bad decisions. I was the third of four kids, which meant my stomach growled so often it was basically a second heartbeat. Living the dream.

**Success from Manofresh (third-person reference for texture feel):**
> So, Truck Gun really had some beef against this guy. His family is pretty trash and he gets bullied for having black Asian hair as an odd thing in this world.

None of those are jokes you'd repeat to a friend. None of them are punchlines. Together they make the script feel alive. That's texture.

**Texture techniques to draw from (rotate, don't repeat):**

- Understatement ("My stomach growled so often it was basically a second heartbeat")
- Casual slang and contractions ("My family was broke broke")
- Ironic contrast ("Turns out I had zero talent. Fantastic.")
- Blunt labeling ("Our house was held together by hope and bad decisions")
- Self-deprecation ("I was middle management in a family of peasants")
- Absurdity acknowledgment ("I got bullied for black hair. Apparently that's a crime here.")
- Dry rhetorical aside as statement ("They declared I had no talent. Cool.")

**What to avoid:**

- No explained jokes. No punchlines. No forced humor.
- No more than one aside every sentence or two — it becomes noise.
- Never add new plot points. Texture is commentary, not content.
- Never repeat a texture motif already logged in the Continuity Tracker.

---

## THE CONTINUITY TRACKER

This is the thing that keeps chunk 12 from contradicting chunk 1. Keep one per novel (not per part — it carries across the whole run). Update it after every chunk.

```
## Continuity Tracker — [Novel Title]

**Progress:** Part [X], Chunk [N] — Chapters [range] processed so far

**POV:** First-person MC. Always "I / me / my."

**MC voice notes:**
- Name (used only in dialogue): [name]
- Tone established: [e.g., dry, hyped, warm, ominous, playful]
- Texture style in use: [e.g., understatement, self-deprecation, blunt labeling]
- Asides used so far: [list to avoid repetition]

**Characters (real name → casual stand-in in use):**
- [Full Name] → [nickname or role-based identifier]
- (Do not include the MC here. The MC is always "I.")

**Established terms/systems (don't re-explain):**
- [power system, item, place, title name] — [what it means, established in chunk N]

**Comparisons/references already used (don't repeat):**
- [list]

**Texture motifs already used (vary these, don't reuse the exact line):**
- [list]

**Chunk boundaries planned:**
- Chunk 1: Chapters [X–Y], ends at [scene break / cliffhanger]
- Chunk 2: Chapters [X–Y], ends at [scene break / cliffhanger]
- (Pre-plan all chunks before generating anything.)

**Where the last chunk left off (1–2 sentences):**
- [state]

**Chunk 1 placeholder margin (keep until Prompt 5 runs):**
- Margin length (1 or 2 sentences, as the AI wrote it): [state which]
- The sentence immediately following the margin, verbatim: "[paste it here as soon as chunk 1 is generated — this is the exact line, whether it turns out to be sentence 2 or sentence 3, that Prompt 5 needs to splice against]"
```

**Nickname collision rule:** Before assigning a new casual stand-in, check the registry above. If the stand-in is already assigned, use a distinct descriptor instead (e.g., "the guild master" vs. "his younger sister," or role-based: "the captain," "the rookie," "the old man").

---

## PROMPT 1 — Universal Narration Engine

**COPY EVERYTHING BELOW THIS LINE**

You are converting a novel into a spoken YouTube narration script. The source is prose fiction. The narration is **first-person from the MC's point of view**. The MC is telling their own story to a friend. Every plot point, every piece of dialogue substance, and every character beat from the source stays. Nothing gets invented.

**POV rule — never broken:**

Use **I / me / my** for the MC. Never "our guy," "my guy," "bro," or the MC's name in narration (except when other characters address the MC in dialogue). If the source has other POVs, convert them to what the MC can observe, infer, or hear. Do not switch into another character's head.

**How this voice behaves:**

It cuts drag, not plot. Drag is redundant description, atmosphere that doesn't affect anything, internal monologue that restates a feeling already shown, and stage-direction wrapping around dialogue ("he said, leaning in, with a sly smile" — keep the line, cut the choreography around it unless the choreography itself matters).

It does not compress to a fixed ratio. Cut dead weight, keep what's alive. Output length is whatever the story needs.

It refers to **other characters** casually. Full name on first introduction, then whatever the MC would naturally call them — a nickname, "my brother," "that guy," "the guild master." Check the Continuity Tracker for what's already assigned. Don't switch nicknames mid-story.

It leans on comparison when earned. If something genuinely evokes a well-known reference (a power, a vibe, a trope), point at it. Never force one. Check the tracker so you're not reusing a comparison already spent.

**Texture rule — never broken:**

Every 2–4 sentences, the MC must have a small, dry, first-person aside — a reaction, judgment, or framing that shows personality. Never explained. Never a punchline. Never more than one aside every sentence or two.

Techniques: understatement, casual slang, ironic contrast, blunt labeling, self-deprecation, absurdity acknowledgment, dry rhetorical statement. Rotate them. Never repeat a texture motif already logged in the tracker.

Failed flat version: "Reborn into a family of peasants, they immediately tell him the kid has zero magical talent. To make matters worse, their house is that classic rundown shack."

Textured version: "So I got reborn into a family of peasants. They took one look at me and declared I had zero magical talent. Classic. Our house was a shack held together by hope and bad decisions."

No new plot points. Texture is commentary, not content.

It sounds spoken. Short, clean sentences. No em-dashes. No semicolons. No nested clauses. Soft paragraph breaks (2–4 sentences) mark natural pauses. No headers, bullets, scene labels, or markdown inside the narration text.

**TTS normalization baked in:**

Write all numbers as words (1984 → nineteen eighty-four). Expand acronyms on first use (NASA → N.A.S.A., or "the space agency"). Avoid homographs that could be misread (read/read, lead/lead). No ellipses or parentheses. Baseline punctuation is comma and period. Question marks, exclamation points, colons, and quotation marks are also allowed. Nothing else.

If a name is genuinely hard to pronounce, give a simple phonetic hint in brackets the first time only (e.g., "Kael [rhymes with 'kale']"). Use sparingly.

If a chunk boundary lands on a natural cliffhanger, end there. Don't add a wrap-up sentence.

**Hard rules — never broken:**

- Never invent a plot point, character action, or detail not in the source.
- Never delete a plot point to save length — compress it instead.
- If two characters share a similar role, give them a distinct, consistent identifier instead of relying on names alone.
- No sign-off, outro, or closing commentary mid-script — only at the true end of a chapter batch if asked for one.
- Output as flowing narration prose only. No headers, no bullets, no scene markers inside the text.

**Before finishing, check your own output:**

- Does every sentence trace back to something in the source?
- Is anything guessed, assumed, or added?
- Does it sound like the MC talking, not a novel being read?
- Is the MC consistently "I / me / my" — never "our guy," "my guy," or their own name?
- Does every 2–4 sentence stretch carry at least one small textured aside?
- Is any name, comparison, or texture line repeated from what the Continuity Tracker says is already used?
- Are numbers written as words? Acronyms expanded? Homographs disambiguated?
- Does the ending respect a cliffhanger if the source had one?

If something fails these checks, fix it before responding.

**END OF PROMPT 1**

---

## PROMPT 2 — The Niche Module

Pick the one that matches this batch of chapters. If the tone shifts mid-novel, switch modules — this is decided chunk by chunk, not once for the whole book.

### MODULE A — Isekai, System, Power Fantasy
**COPY EVERYTHING BELOW**

This batch is isekai / power fantasy / system-driven. The audience already knows the genre conventions — system notifications, leveling, regression, dungeon runs, OP reveals. Use that shorthand when it fits.

Energy: hyped, casual, a little chaotic. The MC's first-person voice quietly roots for themselves while also being aware of the absurdity. Texture leans dry excitement or deadpan disbelief at system nonsense.

Comparisons draw from action movies, superhero franchises, gaming, and well-known anime — whatever the moment genuinely evokes.

Swearing: PG-13, used sparingly for emphasis on big moments only.

**END OF MODULE A**

### MODULE B — Romance, Josei, Emotional Drama
**COPY EVERYTHING BELOW**

This batch is romance or emotional drama. The audience is here for relationship beats and emotional payoff.

Energy: warmer, more invested, less ironic distance. The MC's first-person emotions are direct but not melodramatic. Real names get used more often for the love interest — names carry emotional weight here. Texture leans self-aware, slightly vulnerable.

Asides read as relatable reactions ("you can tell where this is going," "this is the moment everything shifts") rather than jokes.

Comparisons draw from other romance media, relationship shorthand, and tropes the audience already recognizes.

Swearing: rare. Stay warm and accessible.

**END OF MODULE B**

### MODULE C — Dark Action, Revenge, Thriller
**COPY EVERYTHING BELOW**

This batch is dark action, revenge, or thriller. The stakes are serious and the narration should respect that weight.

Energy: still conversational, but restrained. Don't joke through fight scenes or gut-punch moments — asides land harder when they're rare. In heavy beats (a death, a betrayal, long-awaited revenge), use other characters' real names instead of casual stand-ins; it hits harder. The MC is still "I."

Texture leans ominous understatement and consequence, not punchlines. Silence is texture too.

Comparisons can draw from serious action media when genuinely earned — never reach for a comedic reference in a heavy moment.

Swearing: can be slightly stronger for genuine weight, still restrained.

**END OF MODULE C**

### MODULE D — Comedy, Slice of Life, Wholesome
**COPY EVERYTHING BELOW**

This batch is comedy or slice-of-life. Low stakes, character chemistry is the engine.

Energy: lightest of all the modules — playful and casual, leaning into comedy already in the source rather than manufacturing extra jokes. The MC's first-person voice is observational and amused.

Names: use whatever feels most natural, freely mixing real names and casual stand-ins.

Texture leans observational amusement — the MC reacting the way a viewer naturally would.

Comparisons can be playful — sitcoms, social media humor, generational shorthand.

Swearing: rare, comedic emphasis only.

**END OF MODULE D**

---

## PROMPT 3 — Opening Margin Layer (Part 1, Chunk 1 of a NEW novel only)

**COPY EVERYTHING BELOW — use once per novel, never again after the first chunk**

This is chunk 1 of a brand-new novel. The real opening hook is not being written yet — it will be generated later, once the whole part has been processed and its trajectory is known. Writing the hook now, with only this chunk visible, would mean guessing at what deserves the spotlight.

For now, write the first 1–2 sentences of the script as a plain, self-contained, **first-person** establishing statement — who I am and what situation I'm in, stated simply and factually. No hook engineering, no held-back mystery, no stylistic flourish, no attempt to be catchy. Treat this as a placeholder margin.

The placeholder margin must not share a clause, a pronoun reference, or a sentence structure with what follows it. It has to be removable and replaceable later without requiring any edit to anything after it — so make sure the first sentence of normal narration, whichever sentence number that ends up being, stands on its own and doesn't lean grammatically on anything in the margin.

Output the margin inside explicit delimiters so it's unambiguous later:

```
<<<MARGIN_START>>>
[placeholder margin — 1 or 2 sentences, first-person, plain]
<<<MARGIN_END>>>
<<<TARGET_SENTENCE_START>>>
[the sentence immediately following the margin, verbatim]
<<<TARGET_SENTENCE_END>>>
```

State clearly, right after the delimiters, "margin is [1 or 2] sentences." Everything after the margin follows Prompt 1 and the Niche Module exactly as normal.

**Before finishing, check your own output:**

- Is the margin plain and factual, first-person, with no catchy phrasing, rhetorical question, or held-back mystery?
- Would it read exactly the same whether or not a hook gets spliced in front of it later?
- Does it avoid leaning on any word or rhythm doing dramatic work it doesn't need to?
- Does the first sentence after the margin stand on its own, with no clause or pronoun tying it back to the margin?
- Are the delimiters present and correctly placed?

If something fails these checks, fix it before responding.

**END OF PROMPT 3**

---

## PROMPT 3-REPAIR — Margin Repair Pass (use only if the margin reads hook-flavored)

**Use this if, on your skim of chunk 1, the margin has a rhetorical question, held-back-information phrasing ("little did he know," "what happened next..."), or a rhythm/word choice that stands out as dramatic instead of plain. Run it immediately — no need to wait for Part 1 to finish. It touches only the margin; nothing else in chunk 1 changes.**

**COPY EVERYTHING BELOW**

This is a repair pass, not the final hook. The margin below reads more like a hook than a plain establishing statement, which breaks the placeholder rule from Prompt 3. Rewrite only the margin — same length limit as before (1–2 sentences), same instruction: plain, factual, first-person, self-contained, no catchy phrasing, no rhetorical question, no held-back mystery.

The sentence it must still lead into, unchanged, is, verbatim:

"[paste the target sentence — the one immediately following the margin, already logged in the Continuity Tracker]"

The flawed margin to replace is:

"[paste the flawed margin here]"

Output only the corrected margin, wrapped in `<<<MARGIN_START>>>` and `<<<MARGIN_END>>>` delimiters. Nothing else — no explanation.

**END OF PROMPT 3-REPAIR**

---

## PROMPT 4 — Chunk Continuation Layer (every chunk after the first)

**COPY EVERYTHING BELOW**

This is a continuation of the same novel-to-script conversion — not a new story. Apply the same rules as Prompt 1 and the Niche Module already established. Same voice, same density-first editing, same texture rules.

The narration remains **first-person from the MC's point of view**. Use I / me / my for the MC. Do not reintroduce the MC formally. Do not let the voice go flat — maintain the MC's first-person texture. Check the Continuity Tracker for texture motifs already used and vary them.

The Continuity Tracker below shows everything established so far — characters and their assigned casual stand-ins, terms already introduced, comparisons and texture lines already used. Do not reintroduce an established character formally, do not re-explain an established term or system, and do not repeat a comparison or texture line already logged.

Continue directly from where the last chunk left off. No recap of prior events, no re-hook, no sign-off at the end of this chunk unless this is the final chunk of the full novel.

[Paste your filled-in Continuity Tracker here]

**END OF PROMPT 4**

---

## PROMPT 5 — Deferred Hook Generation (run ONCE, after Part 1 is fully assembled)

**COPY EVERYTHING BELOW**

You are writing only the opening 1–2 sentences of this script, replacing a placeholder margin that was written before the rest of the part existed. Below is the full assembled narration for Part 1, all chunks combined — this is for context only, so you can judge which true detail from chunk 1 actually carries the most weight once you can see where the part goes. Do not reference, imply, or foreshadow anything that happens later in the part. State only what chunk 1's own source material establishes — nothing from chunk 2 onward may appear in these 1–2 sentences.

By the end of these 1–2 sentences, the listener should already know the protagonist, the situation, the genre, and the vibe — packed in tight. Treat it like the answer you'd give a friend who asked "so what's this about?" and you only had one breath to answer. Avoid scenery, philosophical framing, slow buildup, or anything that delays naming what's actually happening.

Write in **first-person MC voice**. Match the energy and register of whichever Niche Module (A/B/C/D) this part was written in — the hook should sound like it belongs to the same voice as everything that follows. Carry the same texture as the rest of the script — dry, ironic, deadpan, not a generic opener.

These 1–2 sentences must lead cleanly into the sentence immediately following the deleted margin. That sentence — whether it was originally sentence 2 or sentence 3, per what was logged in the Continuity Tracker — is, verbatim:

"[paste that exact saved sentence here]"

If no 1–2 sentence version reads cleanly into that target, it's fine to write a slightly longer hook rather than force an awkward fit — but state clearly, after the hook, "hook length: [N] sentences" so it's obvious what to splice.

Output the replacement hook inside `<<<HOOK_START>>>` and `<<<HOOK_END>>>` delimiters, followed by the "hook length" line if needed. Nothing else — no explanation, no repetition of the rest of the script.

**Before finishing, check your own output:**

- Does it state only what chunk 1 itself establishes, with nothing pulled from later chunks?
- Does it match the Niche Module's energy rather than reading generic?
- Does it carry the same texture as the rest of the script?
- Does it flow cleanly, with no grammatical seam, into the target sentence above?
- Is it as tight as it can be without cutting genre, protagonist, or situation?

[Paste the full assembled Part 1 narration here]

**END OF PROMPT 5**

---

## PROMPT 6 — Self-Audit Pass (run after every chunk)

**COPY EVERYTHING BELOW**

You are auditing a narration script against its source text. Do not rewrite. Only list.

Given the source text and the narration below, output:

1. **Plot points in the source that are missing from the narration** (list each, one line).
2. **Details in the narration that do not appear in the source** (list each, one line — this catches invention).
3. **Comparisons or texture asides repeated from earlier chunks** (per the Continuity Tracker).
4. **POV violations** — any place the narration slipped out of first-person MC into third-person, another character's head, or used "our guy / my guy / bro" for the MC.
5. **Texture gaps** — any stretch longer than 4 sentences that reads flat (no MC aside).
6. **TTS hazards** — numbers not written as words, unexpanded acronyms, ambiguous homographs, ellipses, parentheses.
7. **Tone drift** — any section that reads like a different module than the one declared.

Output as a numbered list under each header. No prose commentary. No fixes.

[Paste source text for this chunk]

[Paste narration for this chunk]

[Paste Continuity Tracker]

**END OF PROMPT 6**

---

## PROMPT 7 — Fact-Check Pass (run after Prompt 6)

**COPY EVERYTHING BELOW**

You are a fact-checker. You will verify that the narration preserves every major plot point from the source and adds nothing that isn't there.

Step 1: List every major plot point in the source text (aim for 5–10 per chapter). One line each.
Step 2: For each, state "PRESENT" or "MISSING" in the narration, with the narration line that covers it.
Step 3: List any narration sentence that does not trace to a source sentence. Mark each "INVENTED" or "PARAPHRASE."
Step 4: Give a final verdict: "PASS" if no MISSING and no INVENTED; "FAIL" otherwise.

Be strict. A paraphrase that changes meaning counts as INVENTED. A compressed plot point that still conveys the same event counts as PRESENT.

[Paste source text for this chunk]

[Paste narration for this chunk]

**END OF PROMPT 7**

---

## PROMPT 8 — Texture Repair Pass (run only if output reads flat)

**COPY EVERYTHING BELOW**

Rewrite the following lines to add first-person MC texture. Keep all plot points. Add dry, ironic, deadpan asides — one every 2–4 sentences. No jokes. No punchlines. No new plot. Rotate techniques: understatement, casual slang, ironic contrast, blunt labeling, self-deprecation, absurdity acknowledgment, dry rhetorical statement. Do not repeat a texture motif already logged in the Continuity Tracker.

Do not change POV. The MC remains "I / me / my."

Output only the rewritten lines. No explanation.

[Paste flat lines here]

[Paste Continuity Tracker]

**END OF PROMPT 8**

---

## PROMPT 9 — TTS Normalization Pass (run after every chunk)

**COPY EVERYTHING BELOW**

Normalize the following narration for text-to-speech. Do not change plot, POV, or texture. Only fix these:

1. Write all numbers as words (1984 → nineteen eighty-four; 3rd → third; 5% → five percent).
2. Expand acronyms on first use (NASA → N.A.S.A. or "the space agency"; FBI → F.B.I. or "the bureau").
3. Disambiguate homographs likely to be misread (read/read, lead/lead, wind/wind, tear/tear). Rewrite the sentence if needed.
4. Remove ellipses, parentheses, and any punctuation other than comma, period, question mark, exclamation point, colon, and quotation marks.
5. Ensure no em-dashes or semicolons remain.
6. Phonetic hints for hard names stay in brackets — leave them.

Output only the normalized narration. No explanation.

[Paste narration here]

**END OF PROMPT 9**

---

## PROMPT 10 — Tracker Extraction (run after every chunk, after Prompt 9)

**COPY EVERYTHING BELOW**

You are updating the Continuity Tracker for a novel-to-script conversion. Read the finished narration for this chunk and the current Continuity Tracker. List only what this chunk added. Do not repeat anything already in the tracker. Do not rewrite the tracker.

Output one JSON object and nothing else, with exactly these keys:

```
{
  "new_characters": [{"name": "Full Name", "standin": "casual stand-in used in the narration"}],
  "new_terms": [{"term": "term", "meaning": "what it means", "chunk": 1}],
  "new_comparisons": ["comparison or reference used"],
  "new_texture_motifs": ["texture aside used, quoted as written"],
  "chunk_end_state": "1–2 sentences on where this chunk leaves off",
  "nickname_collisions": ["any stand-in in this chunk already assigned to a different character in the tracker"]
}
```

Use an empty list when a key has nothing new. "chunk" is the number of the chunk being processed, given below. Never include the MC in "new_characters". The MC is always "I."

Chunk number: [paste chunk number]

[Paste Continuity Tracker]

[Paste finished narration for this chunk]

**END OF PROMPT 10**

---

## PROMPT 11 — Metadata Extraction (run after every chunk, after Prompt 9)

**COPY EVERYTHING BELOW**

You are marking scene changes in a finished narration script for later image sync. Read the narration below. Each time the scene changes (new location, new time, new major event), record it.

Output one JSON object and nothing else, with exactly this shape:

```
{"scenes": [{"first_sentence": "the first sentence of that scene, copied verbatim from the narration", "description": "one-line description of what is happening"}]}
```

The first scene starts at the first sentence of the narration. Copy "first_sentence" exactly, character for character. Do not add anything to the narration.

[Paste finished narration for this chunk]

**END OF PROMPT 11**

---

## PROMPT 12 — Module Classification (run on each chunk when --module is not given)

**COPY EVERYTHING BELOW**

Below are the four niche modules (A, B, C, D) and the opening of a batch of novel chapters. Decide which module fits this batch best. Output one capital letter, A, B, C, or D, and nothing else.

[Paste the four modules]

[Paste the opening of this chunk]

**END OF PROMPT 12**

---

## CHUNKING STRATEGY

Process 5 chapters per chunk maximum (roughly 7–12k words of source text per pass), not the full 20–25 chapter batch at once. A single AI response covering an entire part would need to generate close to as much text as it read in — 25–30k words of narration in one shot — and quality reliably drifts over an output that long: repeated phrasing, forgotten continuity, degraded texture. Smaller chunks keep output length in a range the model sustains quality across, let you catch and fix problems before they compound, and let you review as you go.

For a 20–25 chapter part, that's roughly 4–5 chunks. Update the Continuity Tracker after each one. Keep chunk 1's raw output on hand (specifically the sentence immediately following its placeholder margin, verbatim) until Prompt 5 runs — you'll need it to splice the real hook in.

**Pre-plan chunk boundaries before generating anything.** Divide the novel at chapter breaks. Chunks exceeding 12k words split at the nearest scene break. A single chapter over 12k words becomes its own chunk. Mark every boundary in the Continuity Tracker. This prevents mid-scene cuts and lets the AI end each chunk on the strongest available cliffhanger.

**Self-audit and fact-check every chunk** (Prompts 6 and 7) before moving on. This is not optional — it's what catches invention and omission before they compound across the whole run.

---

## QUALITY CONTROL WORKFLOW (PER CHUNK)

1. Generate chunk with Prompts 1–4 (or 1–3 for chunk 1).
2. Run Prompt 6 (Self-Audit). Read the list.
3. Run Prompt 7 (Fact-Check). If FAIL, fix the flagged lines manually.
4. If texture reads flat, run Prompt 8 (Texture Repair).
5. Run Prompt 9 (TTS Normalization).
6. Read the final output aloud at 2x. Your ear catches what the passes missed.
7. Update the Continuity Tracker with Prompt 10: new characters, terms, comparisons, texture motifs, chunk-end state. Operator reviews before merge.
8. If chunk 1: verify margin delimiters are clean and the target sentence is logged verbatim.

---

## METADATA FILE (PARALLEL OUTPUT FOR PHASE 2)

Alongside the narration, keep a separate metadata file for future image-sync work. Do not put these markers in the spoken narration.

```
[00:00] SCENE: MC wakes up in dungeon.
[00:45] SCENE: Meets the guild master.
```

Format: `[timestamp] SCENE: [one-line description]`. The narration itself stays clean prose. Phase 2 will use this file to time images to the voiceover.

---

## RECORD-KEEPING FOR FAIR USE

Recap content sits in a legal gray area around transformative use. This system preserves every plot point from the source while changing the prose substantially, adds first-person MC commentary and texture asides, and applies transformative editing — those transformations are part of what fair use arguments typically lean on, but they are not a guarantee.

**Practical steps:**

- Keep every draft, tracker update, and audit pass. This documents the transformation process.
- Add a **transformative commentary layer** where natural: the MC's unique analysis, criticism, or humor that isn't in the source. Texture asides serve this purpose.
- Prefer public domain works or works with permissive licenses when possible.
- Consult an IP lawyer before scaling.
- Worth knowing where your channel stands on this before investing in a full series.

---

## ROADMAP — PHASE 2 (parked, not built yet)

Once the narration script pipeline is solid: a second prompt system takes the finished, timestamped narration script plus the original novel text side by side, and generates image-sync prompts timed to the voiceover — adapted from your AutoEditor doc, re-styled for manhwa-style art instead of doodle-explainer visuals. The metadata file above provides the timing backbone. Share that doc's actual content when you're ready to build this stage.

---

## END-TO-END CHECKLIST (BEFORE HANDING TO CLAUDE)

- [x] POV rule enforced: first-person MC, "I / me / my," never "our guy / my guy / bro / MC name" in narration.
- [x] Texture rule enforced: one small dry first-person aside every 2–4 sentences, techniques listed, failed vs. successful examples included, no jokes, no punchlines, no new plot.
- [x] Continuity Tracker updated: MC voice notes added, POV field added, chunk boundaries field added, nickname collision rule added.
- [x] Prompt 1 updated: first-person, texture rule, TTS normalization baked in, self-check expanded.
- [x] Prompt 2 updated: all four modules reflect first-person MC and texture energy.
- [x] Prompt 3 updated: first-person placeholder margin, delimiters added.
- [x] Prompt 3-REPAIR updated: first-person, delimiters required.
- [x] Prompt 4 updated: first-person continuation, texture maintenance, tracker integration.
- [x] Prompt 5 updated: first-person hook, texture carried, delimiters required.
- [x] Prompt 6 added: Self-Audit pass (fix for flaw #2 and #4).
- [x] Prompt 7 added: Fact-Check pass (fix for flaw #4).
- [x] Prompt 8 added: Texture Repair pass (fix for flaw #2).
- [x] Prompt 9 added: TTS Normalization pass (fix for flaw #6).
- [x] Chunking Strategy updated: 5 chapters max, pre-planned boundaries (fix for flaw #5).
- [x] Quality Control Workflow added: enforces the audit passes (fix for flaw #2).
- [x] Metadata File section added: parallel output for Phase 2 (fix for flaw #8).
- [x] Record-Keeping for Fair Use section added: transformative commentary layer, drafts, lawyer (fix for flaw #7).
- [x] Nickname collision rule added to tracker (fix for flaw #10).
- [x] POV field added to tracker (fix for flaw #9).
- [x] Delimiters added to Prompts 3, 3-REPAIR, and 5 (fix for flaw #3).
- [x] End-to-end checklist confirms all changes are integrated.
