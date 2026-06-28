# Claude Code — Mental Model

## Bộ 6 thứ ảnh hưởng đến hành vi agent

| Thành phần | Mục đích | Format |
|---|---|---|
| **agents** | Subagent chuyên biệt, giới hạn tools + model riêng | Markdown + YAML frontmatter |
| **skills** | Domain knowledge inject vào context khi làm task | Markdown theo cấu trúc |
| **commands** | Slash commands `/tdd`, `/plan`... | Markdown với `description:` |
| **rules** | Always-follow guidelines, auto-load như CLAUDE.md | Markdown |
| **hooks** | Automation trigger trước/sau tool call | JSON config + Node.js script |
| **mcp** | External tool servers (GitHub, browser, DB...) | JSON config |

**Plugin** = cái hộp đựng một hoặc nhiều thứ trong bộ 6 trên. Không phải thành phần thứ 7.

---

## Cấu trúc đầy đủ của project dùng Claude Code

```
project-root/
│
├── CLAUDE.md                         ← project instructions (auto-loaded)
├── .mcp.json                         ← project MCP servers (shared, commit được)
│
└── .claude/
    ├── settings.json                 ← hooks, permissions, plugin IDs (commit được)
    ├── settings.local.json           ← settings riêng máy bạn (gitignore)
    │
    ├── CLAUDE.md                     ← thêm instructions (auto-loaded)
    ├── rules/
    │   └── *.md                      ← thêm instructions (tất cả auto-loaded)
    │
    ├── agents/                       ← project-level agents
    ├── commands/                     ← project-level slash commands
    └── skills/                       ← project-level skills

~/.claude/                            ← user global (tất cả projects)
├── CLAUDE.md                         ← global instructions
├── settings.json                     ← global hooks, permissions
├── settings.local.json               ← global overrides riêng máy
├── rules/*.md                        ← global rules
├── agents/                           ← global agents
├── commands/                         ← global commands
├── skills/                           ← global skills
└── plugins/
    ├── known_marketplaces.json       ← danh sách marketplaces đã đăng ký
    ├── marketplaces/<name>.json      ← cached marketplace manifests
    └── installed/<mkt>/<plugin>/     ← plugin files thực sự
```

**Ghi chú quan trọng:**
- `rules/*.md` là native feature — tất cả `.md` trong `.claude/rules/` tự động load như CLAUDE.md
- Plugins không nằm trong project folder — project chỉ lưu plugin IDs trong `settings.json`
- MCP servers từ `.mcp.json` (project) và `settings.json` (user/global) được **merge**, không override

---

## Scope Loading Priority

```
Thấp → Cao priority (cái sau thắng nếu conflict):

managed   /etc/claude-code/          ← enterprise admin, read-only
   ↓
user      ~/.claude/                 ← global của bạn
   ↓                                   (claude.ai OAuth connectors vào đây)
project   .claude/settings.json      ← shared với team, commit được
   ↓
local     .claude/settings.local.json ← máy bạn, không commit
```

**Agents cụ thể:**
```
builtIn → plugin → user → project → flag → managed
```
Trùng tên → cái có priority cao hơn thắng.

**Commands từ plugin** được prefix tự động: `my-plugin:review` → không collision với `/review` gốc.

---

## Plugin & Marketplace Mechanism

### Hai tầng

```
Tầng 1 — MARKETPLACE (index)
  └── Git repo chứa .claude-plugin/marketplace.json
      marketplace.json = danh sách plugins + link tới từng plugin

Tầng 2 — PLUGIN (nội dung)
  └── Git repo / npm package / local folder
      Chứa agents/commands/skills/hooks/mcp thực sự
```

### Cú pháp install

```
my-plugin @ marketplace-name
   ↑               ↑
tên plugin     alias đã đăng ký trong known_marketplaces.json
```

### Các cách add plugin

**Dùng lệnh trong Claude TUI:**
```bash
# 1. Đăng ký marketplace trước
/plugin marketplace add github:owner/repo-name

# 2. Install plugin từ marketplace
/plugin install exa-search@my-marketplace

# 3. Install thẳng không qua marketplace
/plugin install https://github.com/owner/plugin-repo
/plugin install owner/repo          # GitHub shorthand

# 4. Quản lý
/plugin enable my-plugin
/plugin disable my-plugin
/plugin marketplace list
```

**Chỉ định scope khi install:**
```bash
/plugin install exa-search                   # mặc định: user (global)
/plugin install exa-search --scope project   # ghi vào .claude/settings.json
/plugin install exa-search --scope local     # ghi vào .claude/settings.local.json
```

**Thủ công (không dùng marketplace):**
```bash
# Copy files vào ~/.claude/ trực tiếp
cp -r my-plugin/agents ~/.claude/agents
cp -r my-plugin/commands ~/.claude/commands
# Cách ECC đang dùng với install.sh
```

### Plugin sources được hỗ trợ
| Source | Syntax |
|---|---|
| GitHub | `github:owner/repo` hoặc `owner/repo` |
| Git URL | `https://github.com/owner/repo.git` |
| Git subdirectory | `git-subdir:owner/repo:path/to/plugin` |
| npm | `npm:package-name` |
| Local | `file:./path/to/plugin` |

---

## MCP: Config vs claude.ai Connectors

**Tự config** (trong file):
```json
// .mcp.json hoặc settings.json
{
  "mcpServers": {
    "github": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-github"] }
  }
}
```

**claude.ai Connectors** (Asana, Linear, Notion, Figma...):
- Đến từ account claude.ai khi đăng nhập
- Inject qua remote session proxy (CCR), không phải từ config file
- Cần auth riêng (OAuth) — "Needs Auth" trong UI
- Bạn không cần config gì, chỉ cần connect trong claude.ai account

---

## ECC (everything-claude-code) — Cách nó hoạt động

ECC là plugin dùng `install.sh` để **copy thủ công** vào `~/.claude/`:
- `rules/` → `~/.claude/rules/`
- `agents/` → `~/.claude/agents/`
- `commands/` → `~/.claude/commands/`
- `skills/` → `~/.claude/skills/`

Nó có `.claude-plugin/plugin.json` và `.claude-plugin/marketplace.json` chuẩn format — sẵn sàng serve qua marketplace system trong tương lai, nhưng hiện tại chưa dùng cách đó.
