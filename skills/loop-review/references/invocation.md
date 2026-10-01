# Invocation

Preserve request/target text verbatim. Paths resolve to absolute local paths. Every
mode requires a Git repository. The run root must be outside reviewed repositories.

```json
{
  "schema_version": "2.0",
  "mode": "design",
  "repo": "/absolute/repository",
  "task": {"id": "issue-91", "title": "运行时迁移"},
  "title": "修改方案复核",
  "scope": "full",
  "request": {"kind": "text", "content": "Original user request verbatim"},
  "target": {"kind": "file", "path": "/absolute/repository/docs/proposal.md"}
}
```

`task` and `title` are optional for older callers, but outer agents should provide
readable titles. An omitted task ID creates a new task; titles never imply linkage.
An explicit previous run inherits its task when `task` is omitted. Same Git common
directory means the same repository (including worktrees); separate clones differ.

Targets:

- `design`: `file`, `files` with a nonempty unique `paths` array, or `text` with `content`.
- `code`: `working-tree`, or `git-range` with `base` and optional `head` (default HEAD).
- `review`: target plus `seed_review` (`file` or `text`). Seed claims are part of the
  requested input and are independently verified against the target.

For a complete review of several documents, use e.g. `"target": {"kind": "files",
"paths": ["/repo/docs/proposal.md", "/repo/docs/runtime.md"]}`. Every listed file is
required reading and fingerprinted; applicable nested AGENTS.md files are included.
Do not set one representative target and rely on prose references for mandatory scope.

For a changed version, create a new run. Set `previous_run_id` to explicitly link
unresolved claims from a terminal v2 run of the same repository/task. History is
copied once and fingerprinted; it is not supplied to full-review discovery.
`scope: "fixes"` skips discovery and requires at least one historical claim. Without
explicit history, related task folders remain independent.

`schema_version: "1.0"` invocations remain accepted with v2 execution defaults;
old configuration loop fields do not restore the old iterative workflow.
