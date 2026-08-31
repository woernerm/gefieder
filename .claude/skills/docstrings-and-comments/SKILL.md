---
name: docstrings-and-comments
description: Refactor comments and docstrings so that they become less verbose and get stripped of unimportant information. Use this skill when asked to make comments/docstrings more concise, the same style, or less verbose.
---

# Refactoring comments and docstrings
The goal is that comments and docstrings are short and only contain useful information.
Since comments and docstrings can quickly go out of sync with the code, unnecessary
information is to be avoided, leaving less chance for comments and docstrings to lie.
Comments and docstrings should have the same stye, respectively.

## Process
1. Go through all files and find information in the comments, docstrings and readme 
files that are obviously not true (by checking the code) or obsolete (by checking the 
git history). Correct that information (if not true or obsolete) or remove it (only if 
obsolete). Do not consider other comments, docstrings and readme files as source of 
truth. Just code and third-party documentation are valid sources of truth.

2. For python, comments and docstrings shall be [Google style](https://google.github.io/styleguide/pyguide.html#Comments) 
(fetch and read the resource).

3. After this, go through all python files (do not touch third-party libraries, e.g. do 
not touch files in venv) and refactor the comments and docstrings so that only the most important information, paragraph structure, correct grammar and correct spelling is 
kept. The desired comment and docstrings lengths are:

* *Module/files:* 1 line brief. Description is optional, 15 lines or less.
* *Functions*: 1 line brief. Mandatory for functions/methods that are longer than 
  5 lines. Description is always optional, 10 lines or less. *Args:* section describing 
  all parameters. Mandatory for functions that are longer than 5 lines. Optional 
  otherwise, each less than 5 lines, ideally 1 line. *Returns:* section is optional, 
  at most 3 lines, ideally one line. *Examples:* section is optional, examples shall be 
  distinct (treating totally different use cases only). At most 15 lines, ideally one 
  line. *Raises* section if applicable, at most 5 lines, ideally 1 line.
* *Classes*: 1 line brief. Description is optional, 10 lines or less. *Attributes* 
  section is optional, less than 3 lines per attribute, ideally 1 line.
* *Constants*: Use docstrings for global and class constants, comments otherwise. 
  Docstring/comments are optional. Use only if non-obvious in implementation files.
  Use always in settings and config files. 1 line brief. Description 
  is optional, use only if non-obvious, less than 5 lines, ideally 2 lines or less. 
  *Examples:* section is optional, used to illustrate the format of special strings. 
  Keep examples as distinct as possible. Less than 5 lines, ideally 2 lines.

4. For comments in non-python files use the same length guide but a style consistent
with what is most common for the type of file.

5. Again, go through all files and find information in the comments, docstrings and 
readme files that are obviously not true (by checking the code) or obsolete (by checking 
the git history). Correct that information (if not true or obsolete) or remove it (only 
if obsolete). Do not consider other comments, docstrings and readme files as source of 
truth. Just code and third-party documentation are valid sources of truth.

# Style
- Keep gramatically correct sentences in docstrings and full-line comments.
- Only shorten one line comments by removing fill words. Not content.
- Only shorten docstrings which already have only three lines or less by removing fill words. Not content.  
- Focus on why something is done (design decision rationale). Now how something is done.