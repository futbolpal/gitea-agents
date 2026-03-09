# Agent Worktree Conventions

- All git worktrees must be created under `./.worktrees/` in this repository.
- When creating a new worktree, symlink any `.env*` files from the repo root into the worktree.
  - Example:
    - `ln -sfn ../.env .env`
    - `ln -sfn ../.env.example .env.example`
