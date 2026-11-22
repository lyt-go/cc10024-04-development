# 网页行为采集与内容清洗平台

面向网页行为录制、脚本化采集、内容清洗与结构化输出的数据采集与加工平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 行为脚本 | `behavior_script` | 行为脚本 | 脚本编号、目标站点、录制步骤 |
| 元素定位 | `element_selector` | 元素锚点 | 锚点编号、关联脚本、选择器类型 |
| 数据抽取 | `data_extract` | 抽取模板 | 模板编号、关联脚本、抽取字段 |
| 清洗管道 | `clean_pipeline` | 清洗工序 | 工序编号、输入源、清洗规则 |
| 转换规则 | `transform_rule` | 转换规则 | 规则编号、关联工序、源字段 |
| 质量防护 | `quality_guard` | 质量规则 | 规则编号、检核对象、检核维度 |
| 脚本调度 | `script_schedule` | 调度计划 | 计划编号、关联脚本、触发条件 |
| 登录凭证 | `login_mgr` | 登录凭证 | 凭证编号、目标站点、认证方式 |
| 页面快照 | `page_snapshot` | 页面快照 | 快照编号、采集来源、页面版本 |
| 站点归属 | `site_attribution` | 站点归属 | 归属编号、采集归属、入口地址、落地地址、运行身份 |
| 错误恢复 | `error_recover` | 恢复任务 | 任务编号、关联计划、失败步骤 |
| 页面去重 | `page_dedup` | 去重规则 | 规则编号、去重维度、特征字段 |
| 字段归一化 | `field_normalize` | 归一规则 | 规则编号、关联字段、归一类型 |
| 批次队列 | `batch_queue` | 采集批次 | 批次编号、关联计划、任务数量 |
| 画布配置 | `canvas_config` | 画布配置 | 配置编号、关联站点、分辨率 |
| 导出通道 | `export_channel` | 导出配置 | 通道编号、输出格式、目标路径 |
| 运行历史 | `run_history` | 运行记录 | 记录编号、关联计划、开始时间 |
| Schema校验 | `schema_check` | Schema规则 | 规则编号、目标数据、字段名 |
| 内容缓存 | `content_cache` | 缓存配置 | 配置编号、缓存类型、过期策略 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 站点归属与跳转采集

页面快照只保存页面地址和摘要，跨站跳转时无法回答「这次抓取从哪个入口进来、
落在哪个站点、属于哪一次运行」。`site_attribution` 模块补上正式站点归属清单：

- **一次抓取一行，只记一个主站点**。主站点是采集归属站点：跳转采集归入口站点，
  直接采集归自身站点。入口站点、落地站点分别保留，采集团队按入口核对覆盖
  （`entry_site`），处理正文的团队按落地站点找可复采来源（`landing_site`）。
- **重送不重复计数**。同一次抓取按 `(运行身份, 入口地址, 落地地址)` 判重，
  重送只往 `processing.jsonl` 追加处理记录，`crawl_count` 恒为 1。
- **运行确认后按归属去重**。`run_history` 确认完成时按运行身份取归属清单，
  在**同一次运行内**按正文指纹分组，另一个运行的相同页面不会计入；
  写出 `data/dedup/groups.json|jsonl` 和代表页来源 `representatives.jsonl`。
  代表页优先直接采集，供复采执行回放落地地址。
- **站点隔离**。查看分组/明细、复采、入口回查都要带站点标识，只有持有该次抓取
  采集归属的站点才看得到明细，共享正文时不串号。

主要接口（固定路径，注册在通用资源路由之前）：

| 接口 | 说明 |
| --- | --- |
| `POST /api/site_attribution/captures` | 登记一次/多次抓取，重送只追加处理记录 |
| `GET /api/site_attribution/captures?run_id=` | 采集台账，每次真实抓取一行 |
| `GET /api/site_attribution/manifest?run_id=&entry_site=&landing_site=` | 正式归属清单 |
| `GET /api/site_attribution/lookup?attribution_id=&site=` | 入口回查，站点无归属时 404 |
| `POST /api/site_attribution/dedup` | `{"run_id": ...}` 按运行执行去重 |
| `GET /api/site_attribution/groups?run_id=&site=` | 去重分组（组内明细按站点裁剪） |
| `GET /api/site_attribution/groups/{id}/recrawl?site=` | 复采来源记录 |
| `GET /api/run_history/{id}/dedup?site=` | 运行记录视角的分组与代表页来源 |

每次抓取落盘到 `data/attributions/captures.json`（台账）、`manifest.jsonl`（正式清单）
和 `processing.jsonl`（重送处理记录）；去重产物在 `data/dedup/`。

## 测试

```bash
cd backend
.venv/bin/python tests/test_cross_module.py
.venv/bin/python tests/test_site_attribution.py
```

领域：数据工程
子方向：数据采集、爬虫与RPA
