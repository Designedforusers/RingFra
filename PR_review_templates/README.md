# Claude Code Action Workflow Templates

Ready-to-use GitHub Actions workflows for AI-powered PR reviews using [Claude Code Action](https://github.com/anthropics/claude-code-action).

## Setup (One-Time)

1. **Install the Claude GitHub App**: [github.com/apps/claude](https://github.com/apps/claude)
2. **Add `ANTHROPIC_API_KEY`** as a repository secret (Settings → Secrets → Actions)
3. **Copy your chosen workflow** to `.github/workflows/claude.yml`

Or if you have Claude Code CLI installed:
```bash
/install-github-app
```

## Available Templates

| Template | Description | Best For |
|----------|-------------|----------|
| [interactive.yml](./interactive.yml) | Responds to `@claude` mentions | Teams wanting on-demand help |
| [auto-review.yml](./auto-review.yml) | Reviews every PR automatically | Consistent code review coverage |
| [llm-judge.yml](./llm-judge.yml) | Structured scoring rubric (1-5 scale) | Quality gates, metrics tracking |
| [combined.yml](./combined.yml) | Interactive + auto-review | Most teams (recommended) |

## Usage Examples

### Interactive Mode
Comment on any PR or issue:
```
@claude explain what this function does
@claude find potential bugs in this change
@claude suggest performance improvements
```

### Auto Review
Just open a PR - Claude reviews automatically.

### LLM-as-Judge
Get structured feedback:
```
## PR Evaluation

| Criteria | Score | Notes |
|----------|-------|-------|
| Code Quality | 4/5 | Clean, minor style issues |
| Correctness | 5/5 | Logic is sound |
| Security | 5/5 | No issues found |
| Performance | 3/5 | Consider caching |
| Testing | 4/5 | Good coverage |

**Overall Score: 21/25**

### Verdict: APPROVE
```

## Customization

Edit the `prompt` field in workflows to customize review focus:
```yaml
prompt: |
  Focus on:
  - Security vulnerabilities
  - API design
  - Error handling
```

Use `claude_args` for CLI options:
```yaml
claude_args: "--max-turns 5 --model claude-sonnet-4-5-20250929"
```

## Documentation

- [Claude Code Action Docs](https://docs.anthropic.com/en/docs/claude-code/github-actions)
- [GitHub Repository](https://github.com/anthropics/claude-code-action)
