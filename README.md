# 深圳大运中心演出日历

自动汇总**深圳大运中心体育场**和**深圳大运中心体育馆**的演出档期，每天定时更新，生成一个可在手机和电脑上直接打开的静态网页。

---

## 先说结论：哪些源能抓，哪些抓不了

这不是拍脑袋的选型，是逐个实测过的。票务平台的反爬强度差别极大：

| 数据源 | 实测结果 | 是否采用 |
|---|---|---|
| **广东省文旅厅 审批公告** | 纯 HTML，无反爬，含演出名称、日期、场所、举办单位 | ✅ **主力源** |
| 广东省文旅厅 审批公告（移动版 `mindex*.html`） | 与桌面版同一份内容，已作为自动回退路径 | ✅ 回退 |
| 广东省行政执法信息公示平台 | 接口已完整逆向，但 `/appr-law-datacenter-service/law/datacenter/result/list` 实测返回 **503**，真实浏览器里同样如此（页面显示"暂无数据"）。且请求带 AES 加密的 `decryptText` 参数 | ⏸ 已实现，默认关闭 |
| 猫眼（show.maoyan.com） | 页面能打开，但只返回上海数据，URL 里的城市参数不生效，且按 IP 定位城市 | ❌ 云端不可靠 |
| 大麦（search.damai.cn） | 接口实测返回阿里 x5sec 滑块验证页；主站直接超时 | ❌ 需要签名 + Cookie 池 + 代理 |
| 秀动 / 聚橙 | 页面可解析，但**场馆库里没有大运中心**（它们主营 Livehouse 和剧院） | ❌ 无相关数据 |
| 深圳本地宝 | 命中 WAF，跳转到 `reason=highrisk` 拦截页 | ❌ 需要真实浏览器 |
| 深圳市文旅局 | 公示栏目为「营业性演出许可证」（企业资质），非演出档期 | ❌ 字段不对 |
| 大运中心官网 | 站点已下线 / 502 | ❌ |

### 关于第二个网站（行政执法信息公示平台）

你给的 `http://www.xzzfxxgs.gdsf.gov.cn/ApprLawPublicity/result.html` 是个 Vue 单页应用，
我把它的接口完整拆出来了——浏览器实际发出的请求是：

```
GET /appr-law-datacenter-service/law/datacenter/result/list
    ?r=<随机数>&flag=&pageIndex=1&pageRowNum=10&itemType=4
    &belongArea=440000&decisionNum=&legalDepName=&caseName=&decryptText=<AES密文>
```

`itemType`：`4`=行政许可、`1`=行政处罚、`2`=行政强制、`5`=行政检查。

但有两个绕不过的问题：

1. **平台该服务当前是挂的。** 这个请求在真实浏览器里也返回 503，页面显示「暂无数据」。
   同站 `/appr-law-publicity-service/law/publicity/body/list` 却正常返回 200，
   说明只有 datacenter 这一组接口故障——不是我们被反爬拦了。
2. **请求带 AES 加密参数。** `decryptText` 由前端加密生成，密钥线索在 `js/def.js`
   的 `_d / _e / _f` 三个 base64 常量里。因为接口一直 503，没法做对照实验来验证加密逻辑。

适配器已经写好了（`scraper/sources/gd_law_publicity.py`），默认不启用。等平台恢复后：

```bash
python scraper/build.py --sources gd,manual,law
```

另外提醒一句：这个平台公示的是**许可决定记录**（决定书文号、案件名称、行政相对人、公示日期、执法主体），
一般**不含演出档期和演出场所**。所以它的定位是「发现线索」，拿到完整场次还是得靠省厅公告。


**为什么广东省文旅厅是主力源**

按《营业性演出管理条例》，港、澳、台及外国艺人参加的营业性演出必须由**省级**文旅部门审批并公示。公告详情页是一张规整的表单：

```
演出名称     HENRY刘宪华2026 ENJOY THE SHOW 华丽宪场巡回演唱会-深圳站
举办单位     ...
本地演出日期  2026-09-26
演出场所     广东省深圳市龙岗区龙翔大道深圳大运中心体育馆
```

所以这个源能在**开票之前**就拿到档期，而且结构稳定、没有反爬。缺点是覆盖不到纯内地艺人（那由区级部门审批，不上网公示）——这部分靠下面的「人工补录」兜住。

---

## 目录结构

```
dayun-events/
├── scraper/
│   ├── common.py              # HTTP 抓取、场馆归一化、日期解析、跨源去重
│   ├── build.py               # 主入口：跑源 → 过滤 → 合并 → 出数据 → 推提醒
│   ├── notify.py              # 更新提醒（PushPlus / Server酱 / 邮件）
│   └── sources/
│       ├── gd_culture.py      # 广东省文旅厅审批公告（主力，桌面版+移动版互为回退）
│       ├── gd_law_publicity.py# 广东省行政执法信息公示平台（已实现，默认关闭）
│       └── manual.py          # 人工补录表
├── data/
│   ├── events.json            # 抓取结果（自动生成）
│   ├── cache.json             # 公告缓存：URL→事件。既做去重，也是历史库（自动生成）
│   ├── new_events.json        # 本轮新增（自动生成）
│   └── manual.json            # ★ 手工补录，你自己维护
├── site/
│   ├── index.html             # 网页模板，零依赖
│   └── events.json            # 网页读取的数据（构建时复制过来）
├── 大运中心演出日历.html        # ★ 单文件版：数据已内联，双击即开（自动生成）
└── .github/workflows/update.yml
```

全部只用 Python 标准库，没有第三方依赖，不需要 `pip install`。

**默认只保留 2026-09-01 及以后的演出档期**（可用 `--since` 改）。

---

## 本地跑一次

```bash
cd dayun-events
python scraper/build.py --pages 4
```

跑完有两种打开方式：

- **`大运中心演出日历.html`** —— 数据已经内联进页面，**双击直接打开**就能看，不需要任何服务器。
  想发给朋友、放到手机里离线看，用这个。
- `site/index.html` —— 需要和 `site/events.json` 放在一起并由服务器访问
  （浏览器不允许 `file://` 下读取本地 JSON），平时用于部署上线。

常用参数：

| 参数 | 作用 |
|---|---|
| `--pages N` | 抓取审批公告的页数，每页约 20 条。首次建议 `--pages 30` 回补历史，之后默认 10 就够 |
| `--since YYYY-MM-DD` | 只保留该日期及以后的档期，默认 `2026-09-01` |
| `--sources gd,manual,law` | 指定启用的源，默认 `gd,manual`（`law` 见上文说明） |
| `--max-age-days N` | 公告发布日期超过 N 天就跳过详情请求，默认 240，回补时用来提速 |
| `--rebuild` | 忽略缓存全量重抓 |
| `--no-notify` | 只更新数据，不发提醒 |

> 首次运行会逐条抓取公告详情页（每次请求间隔约 1 秒，是刻意放慢以免给源站压力），
> 十几页大概需要 2～4 分钟。之后有了 `data/cache.json`，每天只处理新增的几条，几秒钟就跑完。
>
> 缓存里存的是「公告 URL → 解析出的事件」，所以历史命中的演出不会因为公告翻页而丢失。

---

## 部署到 GitHub（云端定时 + 在线访问）

1. 在 GitHub 新建一个仓库，把 `dayun-events` 里的内容推上去：

   ```bash
   cd dayun-events
   git init && git add . && git commit -m "init"
   git branch -M main
   git remote add origin git@github.com:<你的用户名>/dayun-events.git
   git push -u origin main
   ```

2. 仓库 **Settings → Pages**，`Source` 选 **GitHub Actions**。

3. 到 **Actions** 页，选「更新大运中心演出数据」，点 **Run workflow**，
   `pages` 填 `30` 先把历史公告回补一遍。等它跑完，Pages 地址就是你的网站。

之后每天北京时间 09:00 会自动更新一次。默认只保留 `2026-09-01` 以后的档期，
想改时间窗就在 workflow 里给 `build.py` 加 `--since`。

---

## 配置更新提醒

都是可选的，不配就静默跳过。在仓库 **Settings → Secrets and variables → Actions** 里加：

**方式一：微信推送（推荐，最省事）**

去 [pushplus.plus](https://www.pushplus.plus/) 用微信扫码登录，拿到 token：

| Secret | 值 |
|---|---|
| `PUSHPLUS_TOKEN` | 你的 token |

或者用 [Server 酱](https://sct.ftqq.com/)：加 `SERVERCHAN_KEY`。

**方式二：邮件**

| Secret | 示例 |
|---|---|
| `SMTP_HOST` | `smtp.qq.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | `you@qq.com` |
| `SMTP_PASS` | 邮箱的**授权码**，不是登录密码 |
| `MAIL_TO` | 收件邮箱，多个用逗号分隔 |

配好之后，一旦发现新演出，就会推送一条包含演出名称、场馆、档期、开票时间的提醒。

> 首次运行不会发提醒（避免一次性刷几百条）。之后每次只推真正新增或信息有变化的。

---

## 补录内地艺人的演出

省级公告只覆盖涉港澳台/涉外演出。内地艺人的场次，看到消息后直接在 `data/manual.json` 里加一条，
push 上去会自动重建并部署：

```json
{
  "name": "某某某 2026 巡回演唱会 深圳站",
  "venue": "gym",
  "dates": ["2026-11-08", "2026-11-09"],
  "price": "380 / 680 / 980",
  "on_sale": "10月20日 12:00 开售",
  "url": "购票或公告链接",
  "note": "备注，可留空"
}
```

- `venue` 填 `stadium`（体育场）或 `gym`（体育馆），留空则按名称里的关键词自动判断。
- 只填 `dates` 首尾也行，会自动记录为一段档期。
- 同一场演出如果同时被审批公告和补录表收录，会自动合并成一条，来源标签并列显示。

---

## 抓取能力再往上提的方案

如果哪天你想把大麦、猫眼也吃进来，唯一现实的路子是用**真实浏览器**（Playwright）去渲染页面，
在小规模、低频的前提下拿到数据。大致改动：

1. `pip install playwright && playwright install chromium`
2. 新增 `scraper/sources/playwright_base.py`，封装一个带持久化 Cookie 目录的 browser context
3. 大麦走搜索页而不是 `searchajax` 接口，让浏览器自己过 x5sec 校验
4. 猫眼把城市切成深圳后再抓列表

代价是：GitHub Actions 每次要多花 1～2 分钟，而且平台一改版就可能失效，需要跟着维护。
当前架构已经把数据源做成可插拔的（每个源就是一个返回 `list[dict]` 的 `collect()`），
加新源只需要写一个文件并在 `build.py` 里注册，不用动其他代码。

---

## 免责声明

本项目仅聚合公开的政府审批公示信息与公开渠道消息，用于个人查询演出档期。
所有数据以演出主办方及官方票务平台的最终公告为准，本项目不提供任何票务服务。
抓取时已设置请求间隔，请勿调高频率给源站造成压力。
