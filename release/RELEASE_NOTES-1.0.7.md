# EOS 1.0.7 - Windows x64

Patch release. Unsigned.

## Fixed
- A write to a file at the root of a multi-project workspace (or in a dot folder such as `.tmp`) mapped every project in it and could exceed the gate's time limit. The workspace's own map is now its top-level files.
- Includes the new-project fixes of 1.0.6.
