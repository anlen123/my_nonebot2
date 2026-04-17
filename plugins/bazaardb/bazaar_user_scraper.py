#!/usr/bin/env python3
"""
大巴扎排位数据查询工具
通过用户名查询排位分数历史、赛季统计，生成 JSON + HTML + PNG 卡片
用法: python bazaar_user_scraper.py <用户名> [赛季ID] [输出目录]
示例: python bazaar_user_scraper.py xikala 14
"""

import sys
import os
import json
import asyncio
from urllib.parse import quote
from playwright.async_api import async_playwright

BASE_URL = "https://bazaar.mrmao.life"


async def fetch_json(page, url: str):
    """通过 Playwright page.request 发起 HTTP GET 获取 JSON"""
    resp = await page.request.get(url)
    return await resp.json()


def calc_stats(history: list) -> dict:
    """从历史记录计算统计指标"""
    if not history:
        return {}

    up_games = 0
    down_games = 0
    season_highest = 0
    cur_streak = 0       # 当前连续上分
    max_streak = 0       # 最大连续上分

    for i, item in enumerate(history):
        r = item["rating"]
        if r > season_highest:
            season_highest = r
        if i > 0:
            prev = history[i - 1]["rating"]
            if r > prev:
                up_games += 1
                cur_streak += 1
                if cur_streak > max_streak:
                    max_streak = cur_streak
            elif r < prev:
                down_games += 1
                cur_streak = 0

    total_games = up_games + down_games
    win_rate = (up_games / total_games * 100) if total_games > 1 else 0

    latest = history[-1]
    return {
        "current_rating":  latest["rating"],
        "current_rank":    latest.get("position", "-"),
        "total_games":     total_games,
        "up_games":        up_games,
        "season_highest":  season_highest,
        "win_rate":        round(win_rate, 2),
        "cur_streak":      cur_streak,
        "max_streak":      max_streak,
        "first_record":    history[0]["timestamp"],
        "last_record":     latest["timestamp"],
        "record_count":    len(history),
    }


def build_html(username: str, season_id: str, season_display: str,
               stats: dict, title_info: dict, history: list) -> str:
    """构建完整 HTML 页面（含 ECharts 图表）"""

    # 图表数据
    timestamps = [h["timestamp"] for h in history]
    ratings = [h["rating"] for h in history]
    positions = [h.get("position", None) for h in history]

    valid_ratings = [r for r in ratings if r is not None]
    min_r = max(min(valid_ratings) - 20, 0) if valid_ratings else 0
    max_r = max(valid_ratings) if valid_ratings else 1000

    # 统计卡片
    s = stats
    title_name = title_info.get("titleName", "")
    title_msg = title_info.get("message", "")

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=1100">
<title>{username} - 大巴扎排位数据</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #13131f;
    font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;
    padding: 24px;
    display: inline-block;
    min-width: 1050px;
  }}

  /* ══ 主容器 ══ */
  .wrap {{
    background: #1a1a2e;
    border-radius: 12px;
    box-shadow: 0 2px 20px rgba(0,0,0,.4);
    overflow: hidden;
    width: 1040px;
  }}

  /* ══ 头部 ══ */
  .header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    padding: 22px 28px;
    display: flex; align-items: center; justify-content: space-between;
  }}
  .header-left {{ display: flex; align-items: center; gap: 16px; }}
  .avatar {{
    width: 56px; height: 56px; border-radius: 50%;
    background: rgba(255,255,255,.2);
    border: 2px solid rgba(255,255,255,.5);
    display: flex; align-items: center; justify-content: center;
    font-size: 26px;
  }}
  .user-info h1 {{ font-size: 24px; font-weight: 700; margin-bottom: 4px; }}
  .user-info .season {{ font-size: 13px; opacity: .85; }}
  .title-badge {{
    background: rgba(255,215,0,.25); border: 1px solid rgba(255,215,0,.5);
    border-radius: 20px; padding: 6px 16px; font-size: 14px; font-weight: 600;
  }}
  .msg-badge {{
    background: rgba(255,255,255,.15); border-radius: 20px;
    padding: 6px 16px; font-size: 13px; margin-left: 10px;
  }}

  /* ══ 统计网格 ══ */
  .stats-grid {{
    display: grid; grid-template-columns: repeat(5, 1fr);
    gap: 1px; background: #333; margin: 20px 24px 0;
    border-radius: 10px; overflow: hidden;
  }}
  .stats-grid-row2 {{
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 1px; background: #333; margin: 1px 24px 0;
    border-radius: 0 0 10px 10px; overflow: hidden;
  }}
  .stat-cell {{
    background: #1e1e2e; padding: 16px 12px; text-align: center;
  }}
  .stat-label {{ font-size: 12px; color: #aaa; margin-bottom: 6px; }}
  .stat-value {{ font-size: 26px; font-weight: 700; }}
  .c-blue   {{ color: #5b8dee; }}
  .c-green  {{ color: #52c41a; }}
  .c-red    {{ color: #ff4d4f; }}
  .c-gold   {{ color: #faad14; }}
  .c-purple {{ color: #b37feb; }}
  .c-orange {{ color: #ff7a45; }}

  /* ══ 图表区 ══ */
  .chart-section {{ margin: 20px 24px; }}
  .chart-title {{
    font-size: 16px; font-weight: 700; color: #ccc;
    margin-bottom: 12px; padding-left: 8px;
    border-left: 4px solid #667eea;
  }}
  #chart {{ width: 100%; height: 420px; }}

  /* ══ 底部信息 ══ */
  .footer {{
    text-align: center; padding: 14px; color: #666; font-size: 12px;
    border-top: 1px solid #2a2a3e; margin-top: 4px;
  }}
</style>
</head>
<body>
<div class="wrap">

  <!-- 头部 -->
  <div class="header">
    <div class="header-left">
      <div class="avatar">🎮</div>
      <div class="user-info">
        <h1>{username}</h1>
        <div class="season">{season_display}</div>
      </div>
    </div>
    <div style="display:flex;align-items:center;">
      {'<span class="title-badge">🎖️ ' + title_name + '</span>' if title_name else ''}
      {'<span class="msg-badge">' + title_msg + '</span>' if title_msg else ''}
    </div>
  </div>

  <!-- 统计第一行 -->
  <div class="stats-grid">
    <div class="stat-cell">
      <div class="stat-label">🔍 当前分数</div>
      <div class="stat-value c-blue">{s.get('current_rating', '-')}</div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">🔎 当前排名</div>
      <div class="stat-value c-blue">{s.get('current_rank', '-')}</div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">🎮 总游玩局数</div>
      <div class="stat-value c-purple">{s.get('total_games', '-')}</div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">📈 上分局数</div>
      <div class="stat-value c-green">{s.get('up_games', '-')}</div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">🏆 赛季最高分</div>
      <div class="stat-value c-red">{s.get('season_highest', '-')}</div>
    </div>
  </div>
  <!-- 统计第二行 -->
  <div class="stats-grid-row2">
    <div class="stat-cell">
      <div class="stat-label">✨ 上分率</div>
      <div class="stat-value c-gold">{s.get('win_rate', '-')}<span style="font-size:16px">%</span></div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">🔥 当前连续上分</div>
      <div class="stat-value c-orange">{s.get('cur_streak', '-')}</div>
    </div>
    <div class="stat-cell">
      <div class="stat-label">🚀 最大连续上分</div>
      <div class="stat-value c-red">{s.get('max_streak', '-')}</div>
    </div>
  </div>

  <!-- 图表 -->
  <div class="chart-section">
    <div class="chart-title">📈 用户排位分数历史</div>
    <div id="chart"></div>
  </div>

  <!-- 底部 -->
  <div class="footer">
    数据来源：bazaar.mrmao.life &nbsp;|&nbsp; 记录数: {len(history)} &nbsp;|&nbsp;
    {s.get('first_record', '')} ~ {s.get('last_record', '')}
  </div>

</div>

<script>
const chartDom = document.getElementById('chart');
const chart = echarts.init(chartDom);

const timestamps = {json.dumps(timestamps)};
const ratings = {json.dumps(ratings)};
const positions = {json.dumps(positions)};

chart.setOption({{
  tooltip: {{
    trigger: 'axis',
    backgroundColor: 'rgba(255,255,255,0.95)',
    borderColor: '#ddd',
    textStyle: {{ color: '#333' }},
    formatter: function(params) {{
      let s = params[0].axisValue;
      params.forEach(p => {{
        if (p.seriesName === '排名' && p.value != null) {{
          s += '<br/>' + p.marker + ' 排名: 第 ' + p.value + ' 名';
        }} else if (p.seriesName === '排位分数') {{
          s += '<br/>' + p.marker + ' 分数: ' + p.value;
        }}
      }});
      return s;
    }}
  }},
  legend: {{ data: ['排位分数', '排名'], top: 0 }},
  grid: {{ left: 60, right: 60, top: 40, bottom: 80 }},
  xAxis: [{{
    type: 'category',
    data: timestamps,
    axisLabel: {{ rotate: 35, fontSize: 10, color: '#999' }},
    axisLine: {{ lineStyle: {{ color: '#ccc' }} }},
  }}],
  yAxis: [
    {{
      type: 'value',
      name: '排位分数',
      min: {min_r},
      max: {max_r},
      position: 'left',
      axisLine: {{ show: true, lineStyle: {{ color: '#5470c6' }} }},
      splitLine: {{ lineStyle: {{ color: '#f0f0f0' }} }},
    }},
    {{
      type: 'value',
      name: '排名',
      inverse: true,
      axisLine: {{ show: true, lineStyle: {{ color: '#91cc75' }} }},
      splitLine: {{ show: false }},
      axisLabel: {{ formatter: '{{value}}' }},
    }}
  ],
  dataZoom: [
    {{ type: 'inside', start: 0, end: 100 }},
    {{ type: 'slider', start: 0, end: 100, bottom: 5, height: 24 }}
  ],
  series: [
    {{
      name: '排位分数',
      type: 'line',
      yAxisIndex: 0,
      data: ratings,
      showSymbol: false,
      lineStyle: {{ width: 2.5, color: '#5470c6' }},
      areaStyle: {{
        color: {{
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            {{ offset: 0, color: 'rgba(84,112,198,0.25)' }},
            {{ offset: 1, color: 'rgba(84,112,198,0.02)' }}
          ]
        }}
      }},
    }},
    {{
      name: '排名',
      type: 'line',
      yAxisIndex: 1,
      data: positions,
      showSymbol: false,
      lineStyle: {{ width: 2, color: '#91cc75' }},
    }}
  ]
}});
</script>
</body>
</html>"""


async def scrape_and_export(username: str, season_id: str = "14", out_dir: str = "."):
    safe_name = username.replace("/", "_").replace("\\", "_")
    json_path = os.path.join(out_dir, f"bazaar_{safe_name}_s{season_id}.json")
    html_path = os.path.join(out_dir, f"bazaar_{safe_name}_s{season_id}.html")
    png_path = os.path.join(out_dir, f"bazaar_{safe_name}_s{season_id}.png")

    print(f"🔍 查询用户: {username}  |  赛季: Season {int(season_id)-1}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            viewport={"width": 1100, "height": 900},
        )

        page = await context.new_page()

        # ── 1. 调 API 获取数据 ──
        encoded_user = quote(username)
        hist_url = f"{BASE_URL}/api/rating-history?username={encoded_user}&seasonId={season_id}"
        title_url = f"{BASE_URL}/api/user-season/title/by-username/{encoded_user}/{season_id}"

        print(f"  📡 请求: {hist_url}")
        history = await fetch_json(page, hist_url)

        if not history:
            print(f"  ⚠ 未找到该用户在 Season {int(season_id)-1} 的排位记录（仅记录传奇段位以上）")
            await browser.close()
            return

        print(f"  ✓ 历史记录: {len(history)} 条")

        # 称号信息
        try:
            title_info = await fetch_json(page, title_url)
        except Exception:
            title_info = {}

        if title_info.get("titleName"):
            print(f"  🎖️ 称号: {title_info['titleName']} — {title_info.get('message','')}")

        # ── 2. 计算统计 ──
        stats = calc_stats(history)
        print(f"  📊 当前分数: {stats['current_rating']}  |  排名: #{stats['current_rank']}  |  "
              f"总局数: {stats['total_games']}  |  上分率: {stats['win_rate']}%")

        # ── 3. 保存 JSON ──
        output_data = {
            "username": username,
            "season_id": int(season_id),
            "season_display": f"Season {int(season_id)-1}",
            "title_info": title_info,
            "stats": stats,
            "history": history,
        }
        print(f"\n[2/4] JSON → {json_path}")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        # ── 4. 生成 HTML ──
        season_display = f"Season {int(season_id)-1}"
        html_content = build_html(username, season_id, season_display, stats, title_info, history)
        print(f"[3/4] HTML → {html_path}")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_content)

        # ── 5. 截图 PNG ──
        print(f"[4/4] 截图 → {png_path}")
        card_page = await context.new_page()
        abs_html = os.path.abspath(html_path)
        await card_page.goto(f"file://{abs_html}", wait_until="domcontentloaded")
        try:
            await card_page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        # 等 ECharts 渲染完
        await card_page.wait_for_timeout(1200)

        size = await card_page.evaluate(
            "({w: document.body.scrollWidth, h: document.body.scrollHeight})"
        )
        await card_page.set_viewport_size({"width": size["w"] + 40, "height": size["h"] + 40})
        await card_page.wait_for_timeout(500)
        await card_page.screenshot(path=png_path, full_page=True)
        await card_page.close()
        await browser.close()

    print(f"\n✅ 完成！")
    print(f"   JSON : {json_path}")
    print(f"   HTML : {html_path}")
    print(f"   PNG  : {png_path}")


async def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "xikala"
    season_id = sys.argv[2] if len(sys.argv) > 2 else "14"
    out_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(os.path.abspath(__file__))
    await scrape_and_export(username, season_id, out_dir)


if __name__ == "__main__":
    asyncio.run(main())
