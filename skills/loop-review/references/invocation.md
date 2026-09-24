# Invocation contract

Use JSON. The outer agent preserves user text verbatim.

## Design

```json
{
  "schema_version": "1.0",
  "mode": "design",
  "repo": "/absolute/repo",
  "request": {"kind": "text", "content": "user request verbatim"},
  "target": {"kind": "file", "path": "/absolute/repo/docs/design.md"}
}
```

`target.kind` may be `text` with a `content` field.

## Code: working tree

```json
{
  "schema_version": "1.0",
  "mode": "code",
  "repo": "/absolute/repo",
  "request": {"kind": "text", "content": "review current changes"},
  "target": {"kind": "working-tree"}
}
```

## Code: Git range

```json
{
  "schema_version": "1.0",
  "mode": "code",
  "repo": "/absolute/repo",
  "request": {"kind": "text", "content": "review this branch"},
  "target": {"kind": "git-range", "base": "main", "head": "HEAD"}
}
```

## Existing review

```json
{
  "schema_version": "1.0",
  "mode": "review",
  "repo": "/absolute/repo",
  "request": {"kind": "text", "content": "verify this review"},
  "target": {"kind": "file", "path": "/absolute/repo/docs/design.md"},
  "seed_review": {"kind": "file", "path": "/absolute/review.md"}
}
```

All file paths must be absolute after `~` expansion. The controller hashes file inputs and Git state.
