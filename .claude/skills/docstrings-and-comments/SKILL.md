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
For python, comments and docstrings shall be [Google style](https://google.github.io/styleguide/pyguide.html#Comments) 
(fetch and read the resource).

After this, go through all python files and refactor the comments and docstrings so that
only the most important information, paragraph structure, correct grammar and correct
spelling is kept. The desired comment and docstrings lengths are:

* *Module/file docstring:* 1 line brief. Description is optional, 15 lines or less.
* *Function docstring*: 1 line brief. Description is optional, 10 lines or less. 
  *Args:* section describing all parameters, each less than 5 lines, ideally 1 line. 
  *Returns:* section is optional, at most 3 lines, ideally one line. *Examples:* section 
  is optional, examples shall be distinct (treating totally different use cases only). 
  At most 15 lines, ideally one line. *Raises* section if applicable, at most 5 lines,
  ideally 1 line.
* *Classes*: 1 line brief. Description is optional, 10 lines or less. *Attributes* 
  section is optional, less than 3 lines per attribute, ideally 1 line.
* *Constants*: Docstring is optional. Use only if non-obvious in implementation files.
  Use always in settings and config files. 1 line brief. Description 
  is optional, use only if non-obvious, less than 5 lines, ideally 2 lines or less. 
  *Examples:* section is optional, used to illustrate the format of special strings. 
  Keep examples as distinct as possible. Less than 5 lines, ideally 2 lines.

For comments in non-python files use the same length guide but a style consistent
with what is most common for the type of file.