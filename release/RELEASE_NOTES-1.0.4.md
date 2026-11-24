# EOS 1.0.4 - Windows x64

Patch release. Unsigned.

## Fixed
- On a large project the OpenCode guard gave up after 8-12 s and reported its own timeout as a write DENY; a cut post-write then left the map STALE and blocked the next writes. The guard now waits up to 90 s for the verdict. Expiry is still DENY.
