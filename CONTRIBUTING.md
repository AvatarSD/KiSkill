# Contributing to KiSkill

Thanks for helping agents drive KiCad well. This is a small, opinionated
codebase — the bar is *evidence*, not size of change.

## Ground rules

1. **Never test against a user's real project.** Use `tests/fixtures/` or
   generate a throwaway with `tools/gen_testproj.py`. Real boards may be used
   read-only as soak inputs via the `KX_DONOR_PCB` env var — never committed.
2. **The parser is sacred.** Any change near `kicad_lib/sexp.py` must keep the
   round-trip soak green: `python3 tests/test_roundtrip.py` (token-equal on
   every fixture; byte-identical for `.kicad_sch`/`.kicad_sym`/`.kicad_mod`).
3. **Verify before you write.** New write paths go through the geometric
   verifier (`kicad_lib/verify.py`) and ship with a test that locks the
   behaviour — ideally one that *fails* before your fix.
4. **Skills are documentation, not code.** Each `skills/<name>/SKILL.md` is
   self-contained. Keep engine paths portable: reference `kx` (on PATH) or
   `$(kx root)`, never a hardcoded home directory.

## Running the tests

```bash
git clone https://github.com/AvatarSD/KiSkill.git && cd KiSkill
for t in tests/test_*.py; do echo "== $t"; python3 "$t" || break; done
```

Fast tests are pure Python (parser, geometry, diff, audits). Slower ones
(`test_pcb`, `test_layout`, `test_emsim`) shell out to `kicad-cli`/flatpak,
freerouting, or Docker and **skip cleanly** when the tool is absent.

## Good first contributions

- **Fixtures**: a small, redistributable `.kicad_sch`/`.kicad_pcb` that breaks
  the parser or a verifier rule is gold — add it under `tests/fixtures/` with a
  test that pins the expected behaviour.
- **Rule canon**: forum-sourced ERC/DRC gotchas the tools are blind to. Encode
  them as a `kx <audit>` subcommand + test, and document them in the relevant
  skill. See recent commits for the pattern.
- **Backlog**: open items live in [`doc/BACKLOG.md`](doc/BACKLOG.md). The
  `kicad-improve` skill shows how a tick turns one item into a tested commit.

## Pull requests

- One focused change per PR; explain *why*, and paste the test output.
- Match the surrounding style (no formatter config — read the neighbours).
- By contributing you agree your work is released under the [MIT License](LICENSE).
