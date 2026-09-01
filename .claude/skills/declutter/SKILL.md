---
name: declutter
description: Find and apply refactorings that remove code from this repository — survey for duplication, dead code and obsolete features, then implement fix by fix, measuring the lines each one actually saves and reverting the ones that do not pay. Use when asked to declutter, deduplicate, shrink or refactor the codebase for size, to hunt dead code, or to make comments more concise or to refactor comments.
---

# Decluttering

The goal is fewer code lines (excluding comments and whitespace), not fewer files.
A refactoring done with this skill earns its place by removing more than it adds — 
measured, not estimated.

## Measuring

`.claude/skills/declutter/count-code-lines.sh` counts non-blank, non-comment lines 
across tracked files.

Run it once for a baseline before touching anything, then again after each fix. Never 
report a saving you have not measured.

## Finding Refactoring Opportunities
Assume the repo has grown quite a bit and its time to declutter. Now, its time to find 
both direct and well as indirect/architectural gains through refactoring. Your task is 
to find refactoring opportunities and architectural tweaks that reduce the amount of code.

1. Read the code of this repository to find large sections with literal or 
close-to-literal code duplication. Based on the findings, create a list of the 10
refactoring opportunities ordered by number of code lines a fix likely reduces.
Write these to a temporary markdown file (title, summary, proposed fix, estimated lines
saved) in a chapter called "Literal Code Duplication". Don't fix anything yet.

2. Read the code of this repository to find repeated occurence of the same magic 
string values. Based on the findings, create a list of the 10 most duplicated string 
occurrences, the string with the most duplications first. Append this list to the 
temporary markdown file (title, summary, proposed fix, number of duplications) in a 
chapter called "Magic String Values". Don't fix anything yet.

3. Read these pages and their subpages: 
- https://refactoring.guru/refactoring/smells
- https://refactoring.guru/refactoring/techniques
- https://refactoring.guru/design-patterns/catalog
- https://refactoring.guru/refactoring/catalog
- https://refactoring.guru/refactoring/technical-debt
- https://refactoring.guru/refactoring/what-is-refactoring
- https://refactoring.guru/refactoring/how-to


Then, read the code of this repository again to find refactoring opportunities based on 
the resources above. Create a list of the 10 refactoring opportunities ordered by number 
of code lines a fix likely reduces. Include easy wins for other quality aspects if found, 
but the focus is reducing code. Exclude anything that would be bad practice, bad design 
or very complex regardless of how much it removes. Also consider removing features that 
may not be that useful or even obsolete. Append this list of refactoring opportunities 
to the temporary markdown file (pattern/technique/smell name as title, summary, 
proposed fix, estimated lines saved by the fix, negative sideeffects) in a chapter 
called "Refactoring Opportunities". Don't fix anything yet.

4. Read the temporary markdown file. Sort the findings across chapters, with the fix 
providing the most return of investment first (e.g. little effort, little risk, high 
reduction in code or high quality gain). Present the top 12 in less than 500 words.

## Fixing
Ask the user which findings from the above list shall be fixed. Then, use the 
`count-code-lines.sh` script to measure how many code lines there are now in the entire 
code base (not counting comment-like or empty lines).

Make the fixes the user selected one by one. After each, re-run the counting tool. If a 
fix reduced fewer than 5 lines, revert it. Otherwise run the full test suite and either 
fix small remaining issues or revert the fix entirely if the issues cause more trouble 
than they're worth. If tests are fine, move to the next fix.

Afterwards, list the refactorings implemented/reverted/skipped and the lines each 
actually saved.

Keep a task list of all actions (including refactoring opportunities that you going
to implement) including their status.

## Reporting

List every fix as implemented, reverted or skipped, each with the lines it actually saved,
and give the before/after total. Reverted fixes are results too: say what they cost and
why the rule rejected them. 