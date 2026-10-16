"""Own documents: docx, xlsx, pptx, pdf, md and txt as text, read in a child process with limits (DR-0483 to DR-0485).

`formats` turns a file's bytes into parts, `extract` runs that in a child process and says what came of each file. The
folder source that stages the text (#69's third PR) builds on these two.
"""
