#!/usr/bin/env python3
# web_app.py  —— 软件测试智能体 Web 界面（重构版）
# 启动：python web_app.py  访问 http://localhost:5000

import os, sys, json, glob, threading, queue, time, importlib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template_string, request, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'ta-secret-2024'
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins='*')

test_running = False
test_thread = None

DEFAULT_FRAMEWORK = {
    "project_name": "SQLite3 数据库引擎",
    "language": "C (预编译二进制)",
    "source_files": [],
    "binary": "sqlite3",
    "compile_cmd": "",
    "description": "SQLite3 是世界上使用最广泛的嵌入式关系型数据库引擎，由 C 语言编写。通过命令行工具 sqlite3 可以执行 SQL 语句、管理数据库文件。",
    "test_goals": [
        "测试 NULL 值在各种运算和函数中的行为",
        "测试整数边界值：INT 最大值、最小值",
        "测试 SQL 约束：UNIQUE、NOT NULL 违反时的行为",
        "测试事务：BEGIN/COMMIT/ROLLBACK 的正确性",
        "测试除零：SELECT 1/0",
        "测试批量插入性能",
        "测试聚合函数的正确性",
    ],
    "extra_notes": "sqlite3 命令行工具，使用 :memory: 作为数据库名。",
}

HTML = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Testing Agent</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:ital,wght@0,300;0,400;0,500;1,400&family=Syne:wght@400;500;600;700;800&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --night:#0a0c12;
  --deep:#0e1018;
  --surface:#13161f;
  --raised:#191d28;
  --lift:#1f2433;
  --hover:#252a3a;
  --active:#2d3347;
  --b0:rgba(255,255,255,0.04);
  --b1:rgba(255,255,255,0.08);
  --b2:rgba(255,255,255,0.14);
  --t0:#f0f2f8;
  --t1:#9aa0b8;
  --t2:#535870;
  --t3:#2e3145;
  --violet:#7c6af7;
  --violet-d:#6556e0;
  --violet-g:rgba(124,106,247,0.15);
  --violet-glow:rgba(124,106,247,0.3);
  --cyan:#38c9d4;
  --cyan-g:rgba(56,201,212,0.12);
  --rose:#f472b6;
  --rose-g:rgba(244,114,182,0.12);
  --green:#4ade80;
  --green-g:rgba(74,222,128,0.12);
  --amber:#fbbf24;
  --red:#f87171;
  --red-g:rgba(248,113,113,0.12);
  --sidebar:260px;
  --top:58px;
}
html,body{height:100%;overflow:hidden}
body{
  font-family:'DM Sans',sans-serif;
  background:var(--night);
  color:var(--t0);
  display:flex;
  background-image:
    radial-gradient(ellipse 120% 80% at -10% -20%,rgba(124,106,247,0.07) 0%,transparent 55%),
    radial-gradient(ellipse 80% 60% at 110% 110%,rgba(56,201,212,0.05) 0%,transparent 50%);
}

/* ── SCROLLBAR ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--b2);border-radius:2px}
::-webkit-scrollbar-thumb:hover{background:var(--t2)}

/* ══════════════════════════════════════
   SIDEBAR
══════════════════════════════════════ */
#sidebar{
  width:var(--sidebar);min-width:var(--sidebar);
  background:var(--deep);
  border-right:1px solid var(--b1);
  display:flex;flex-direction:column;
  transition:width .3s cubic-bezier(.4,0,.2,1),min-width .3s cubic-bezier(.4,0,.2,1);
  overflow:hidden;
  position:relative;z-index:20;
  flex-shrink:0;
}
#sidebar.collapsed{width:52px;min-width:52px}

.sb-head{
  padding:0 14px;
  height:var(--top);
  display:flex;align-items:center;gap:10px;
  border-bottom:1px solid var(--b1);
  flex-shrink:0;
}
.sb-logo{
  width:30px;height:30px;min-width:30px;
  background:linear-gradient(135deg,var(--violet),var(--cyan));
  border-radius:8px;
  display:flex;align-items:center;justify-content:center;
  font-family:'Syne',sans-serif;font-size:12px;font-weight:800;
  color:#fff;letter-spacing:-.5px;
  box-shadow:0 0 18px var(--violet-glow),0 2px 8px rgba(0,0,0,.4);
  flex-shrink:0;
}
.sb-brand{
  font-family:'Syne',sans-serif;font-size:13.5px;font-weight:700;
  color:var(--t0);white-space:nowrap;
  transition:opacity .2s,transform .2s;
  letter-spacing:-.2px;
}
.sb-toggle{
  margin-left:auto;background:none;border:none;
  color:var(--t2);cursor:pointer;
  width:26px;height:26px;min-width:26px;
  border-radius:6px;display:flex;align-items:center;justify-content:center;
  transition:all .18s;
}
.sb-toggle:hover{background:var(--lift);color:var(--t0)}
.sb-toggle svg{transition:transform .3s cubic-bezier(.4,0,.2,1)}
#sidebar.collapsed .sb-toggle svg{transform:rotate(180deg)}
#sidebar.collapsed .sb-brand{opacity:0;transform:translateX(-8px);pointer-events:none}

/* Sidebar body */
.sb-body{flex:1;overflow-y:auto;overflow-x:hidden;padding:10px 8px}

.sb-section-label{
  font-size:9.5px;font-weight:600;
  color:var(--t3);text-transform:uppercase;letter-spacing:.12em;
  padding:10px 8px 5px;white-space:nowrap;
  transition:opacity .2s;
}
#sidebar.collapsed .sb-section-label{opacity:0}

.hist-item{
  padding:8px 10px;border-radius:7px;cursor:pointer;
  transition:background .15s;margin-bottom:2px;
  position:relative;white-space:nowrap;overflow:hidden;
  display:flex;align-items:center;gap:8px;
}
.hist-item:hover{background:var(--lift)}
.hist-item.active{background:var(--violet-g)}
.hist-item.active::after{
  content:'';position:absolute;left:0;top:25%;bottom:25%;
  width:2.5px;background:var(--violet);border-radius:0 2px 2px 0;
}
.hist-icon{
  width:7px;height:7px;min-width:7px;border-radius:50%;flex-shrink:0;
  transition:transform .2s;
}
.hist-icon.ok{background:var(--green)}
.hist-icon.ng{background:var(--red)}
.hist-info{flex:1;overflow:hidden;transition:opacity .2s}
.hist-name{font-size:12.5px;font-weight:500;color:var(--t0);overflow:hidden;text-overflow:ellipsis}
.hist-meta{font-size:10.5px;color:var(--t2);font-family:'JetBrains Mono',monospace;margin-top:1px}
#sidebar.collapsed .hist-info{opacity:0}

/* ── Coverage card at bottom ── */
.sb-footer{
  border-top:1px solid var(--b1);padding:12px 10px;
  transition:opacity .2s;
}
#sidebar.collapsed .sb-footer{opacity:0;pointer-events:none}
.cov-title{font-size:9.5px;font-weight:600;color:var(--t2);text-transform:uppercase;letter-spacing:.1em;margin-bottom:8px}
.cov-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:5px}
.cov-label{font-size:11px;color:var(--t2)}
.cov-val{font-size:11px;font-weight:600;color:var(--cyan);font-family:'JetBrains Mono',monospace}
.cov-track{height:3px;background:var(--lift);border-radius:2px;margin-bottom:8px;overflow:hidden}
.cov-bar{height:100%;background:linear-gradient(90deg,var(--violet),var(--cyan));border-radius:2px;transition:width .6s cubic-bezier(.4,0,.2,1)}

/* ══════════════════════════════════════
   MAIN
══════════════════════════════════════ */
#main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}

/* ── TOPBAR ── */
.topbar{
  height:var(--top);min-height:var(--top);
  background:var(--deep);border-bottom:1px solid var(--b1);
  display:flex;align-items:center;padding:0 20px;gap:14px;
  flex-shrink:0;
}
.topbar-title{
  font-family:'Syne',sans-serif;font-size:15px;font-weight:700;
  color:var(--t0);letter-spacing:-.2px;
}
.topbar-sub{font-size:11.5px;color:var(--t2);font-family:'JetBrains Mono',monospace}

.badge{
  display:flex;align-items:center;gap:6px;
  padding:5px 12px;border-radius:20px;
  font-size:11.5px;font-weight:500;
  border:1px solid;transition:all .3s;white-space:nowrap;
}
.badge.idle{color:var(--t2);border-color:var(--b1);background:var(--raised)}
.badge.running{color:var(--violet);border-color:rgba(124,106,247,.3);background:var(--violet-g)}
.badge.done{color:var(--green);border-color:rgba(74,222,128,.3);background:var(--green-g)}
.badge.error{color:var(--red);border-color:rgba(248,113,113,.3);background:var(--red-g)}
.dot-pulse{
  width:7px;height:7px;border-radius:50%;background:currentColor;
  animation:blink 1.4s ease-in-out infinite;
}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}

/* ── SPLIT ── */
#split{flex:1;display:flex;overflow:hidden;min-height:0}

/* Log pane */
#log-pane{
  flex:1;display:flex;flex-direction:column;
  border-right:1px solid var(--b1);overflow:hidden;min-width:0;
}
.pane-bar{
  height:38px;min-height:38px;padding:0 14px;
  border-bottom:1px solid var(--b1);
  display:flex;align-items:center;gap:8px;
  font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--t2);background:var(--surface);flex-shrink:0;
}
.pane-pip{width:6px;height:6px;border-radius:50%}
#log-scroll{flex:1;overflow-y:auto;padding:10px 14px}
.log-line{
  font-family:'JetBrains Mono',monospace;font-size:12px;line-height:1.75;
  animation:fadeUp .12s ease-out;padding:1px 0;
}
@keyframes fadeUp{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
.log-line.info{color:var(--t1)}
.log-line.cmd{color:#a78bfa}
.log-line.out{color:var(--t0)}
.log-line.err{color:var(--amber)}
.log-line.ok{color:var(--green)}
.log-line.fail{color:var(--red)}
.log-line.sec{
  color:var(--violet);font-weight:600;
  margin-top:10px;padding-top:8px;
  border-top:1px solid var(--b1);
}
.log-line.dim{color:var(--t3)}
.log-line.empty{color:var(--t3);font-style:italic}

/* Progress */
#prog-wrap{
  flex-shrink:0;border-top:1px solid var(--b1);
  background:var(--surface);padding:10px 14px;display:none;
}
.prog-row{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.prog-lbl{font-size:11px;color:var(--t1);font-family:'JetBrains Mono',monospace}
.prog-pct{font-size:11px;font-weight:600;color:var(--violet);font-family:'JetBrains Mono',monospace}
.prog-track{height:2.5px;background:var(--lift);border-radius:2px;overflow:hidden;margin-bottom:8px}
.prog-bar{
  height:100%;
  background:linear-gradient(90deg,var(--violet),var(--cyan));
  border-radius:2px;width:0%;
  transition:width .4s cubic-bezier(.4,0,.2,1);
  box-shadow:0 0 10px var(--violet-glow);
}
.prog-stats{display:flex;gap:14px}
.stat{display:flex;align-items:center;gap:5px;font-size:10.5px;font-family:'JetBrains Mono',monospace}
.stat-d{width:5px;height:5px;border-radius:50%}

/* Report pane */
#rpt-pane{
  width:45%;min-width:300px;
  display:flex;flex-direction:column;overflow:hidden;
}
#rpt-scroll{flex:1;overflow-y:auto;padding:22px 26px}

/* Report placeholder */
.rpt-ph{
  height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;
  gap:14px;color:var(--t3);
}
.rpt-ph-icon{
  width:56px;height:56px;
  border:1px solid var(--b1);border-radius:14px;
  display:flex;align-items:center;justify-content:center;
}

/* Markdown in report */
#rpt-scroll h1{
  font-family:'Syne',sans-serif;font-size:19px;font-weight:700;
  color:var(--t0);margin-bottom:18px;padding-bottom:14px;
  border-bottom:1px solid var(--b1);letter-spacing:-.3px;
}
#rpt-scroll h2{
  font-family:'Syne',sans-serif;font-size:13px;font-weight:700;
  color:var(--violet);margin:22px 0 9px;
  text-transform:uppercase;letter-spacing:.07em;
  display:flex;align-items:center;gap:7px;
}
#rpt-scroll h2::before{
  content:'';width:2.5px;height:12px;
  background:var(--violet);border-radius:2px;display:inline-block;
}
#rpt-scroll h3{font-size:13px;font-weight:600;color:var(--t0);margin:14px 0 7px}
#rpt-scroll p{font-size:13px;line-height:1.7;color:var(--t1);margin-bottom:9px}
#rpt-scroll table{width:100%;border-collapse:collapse;margin:10px 0;font-size:12px}
#rpt-scroll th{
  text-align:left;padding:7px 11px;
  background:var(--raised);color:var(--t2);
  font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.07em;
  border-bottom:1px solid var(--b1);
}
#rpt-scroll td{padding:7px 11px;color:var(--t0);border-bottom:1px solid var(--b0);font-size:12px}
#rpt-scroll tr:hover td{background:var(--lift)}
#rpt-scroll code{
  font-family:'JetBrains Mono',monospace;font-size:11.5px;
  background:var(--raised);padding:2px 6px;border-radius:4px;color:var(--cyan);
}
#rpt-scroll pre{
  background:var(--raised);border:1px solid var(--b1);
  border-radius:8px;padding:13px;overflow-x:auto;margin:9px 0;
}
#rpt-scroll pre code{background:none;padding:0;color:var(--t0);font-size:11.5px;line-height:1.65}
#rpt-scroll blockquote{
  border-left:2px solid var(--violet);margin:9px 0;
  padding:8px 13px;background:var(--violet-g);
  border-radius:0 7px 7px 0;font-size:12.5px;color:var(--t1);
}
#rpt-scroll ul,#rpt-scroll ol{padding-left:18px;margin:7px 0}
#rpt-scroll li{font-size:12.5px;line-height:1.7;color:var(--t1);margin-bottom:3px}
#rpt-scroll hr{border:none;border-top:1px solid var(--b1);margin:18px 0}

/* ── RESIZER ── */
#resizer{
  width:3px;cursor:col-resize;flex-shrink:0;
  background:var(--b1);transition:background .2s;position:relative;
}
#resizer:hover,#resizer.active{background:var(--violet)}

/* ══════════════════════════════════════
   INPUT AREA
══════════════════════════════════════ */
#input-area{
  border-top:1px solid var(--b1);
  background:var(--deep);padding:14px 18px;flex-shrink:0;
}
.cfg-row{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.cfg-grp{display:flex;flex-direction:column;gap:4px;flex:1;min-width:140px}
.cfg-lbl{font-size:9.5px;font-weight:600;color:var(--t3);text-transform:uppercase;letter-spacing:.11em}
.cfg-sel,.cfg-inp{
  background:var(--raised);border:1px solid var(--b1);
  border-radius:7px;color:var(--t0);
  font-family:'JetBrains Mono',monospace;font-size:12px;
  padding:7px 10px;outline:none;transition:border-color .2s,box-shadow .2s;width:100%;
}
.cfg-sel:focus,.cfg-inp:focus{
  border-color:rgba(124,106,247,.5);
  box-shadow:0 0 0 3px rgba(124,106,247,.1);
}
.cfg-sel option{background:var(--raised)}

.inp-row{display:flex;gap:10px;align-items:flex-end}
#prompt{
  flex:1;background:var(--raised);border:1px solid var(--b1);
  border-radius:10px;color:var(--t0);
  font-family:'DM Sans',sans-serif;font-size:13.5px;
  padding:11px 15px;outline:none;resize:none;height:76px;
  transition:border-color .2s,box-shadow .2s;line-height:1.55;
}
#prompt::placeholder{color:var(--t3)}
#prompt:focus{
  border-color:rgba(124,106,247,.5);
  box-shadow:0 0 0 3px rgba(124,106,247,.1);
}

.btn-run{
  background:linear-gradient(135deg,var(--violet),#a855f7);
  border:none;color:#fff;
  font-family:'Syne',sans-serif;font-size:13px;font-weight:700;
  padding:0 18px;height:76px;min-width:76px;
  border-radius:10px;cursor:pointer;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;
  transition:all .2s;
  box-shadow:0 4px 20px rgba(124,106,247,.35);
  letter-spacing:.3px;
}
.btn-run:hover:not(:disabled){
  transform:translateY(-2px);
  box-shadow:0 8px 28px rgba(124,106,247,.45);
}
.btn-run:active:not(:disabled){transform:translateY(0);box-shadow:0 2px 10px rgba(124,106,247,.3)}
.btn-run:disabled{opacity:.4;cursor:not-allowed;transform:none;box-shadow:none}
.btn-run svg{width:17px;height:17px}

.btn-stop{
  background:var(--red-g);border:1px solid var(--red);
  color:var(--red);font-family:'DM Sans',sans-serif;
  font-size:12px;font-weight:600;padding:8px 14px;
  border-radius:7px;cursor:pointer;display:none;transition:all .2s;
}
.btn-stop:hover{background:var(--red);color:#fff}
.btn-stop.show{display:block}

/* ── TOAST ── */
#toast{
  position:fixed;bottom:22px;right:22px;
  background:var(--lift);border:1px solid var(--b2);
  border-radius:10px;padding:11px 16px;font-size:13px;color:var(--t0);
  z-index:999;max-width:280px;
  box-shadow:0 8px 32px rgba(0,0,0,.5);
  transform:translateY(16px);opacity:0;
  transition:all .3s cubic-bezier(.4,0,.2,1);
  pointer-events:none;
}
#toast.show{transform:translateY(0);opacity:1}
</style>
</head>
<body>

<!-- ── SIDEBAR ── -->
<nav id="sidebar">
  <div class="sb-head">
    <div class="sb-logo">TA</div>
    <span class="sb-brand">Testing Agent</span>
    <button class="sb-toggle" onclick="toggleSidebar()" title="折叠侧边栏">
      <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M15 18l-6-6 6-6"/>
      </svg>
    </button>
  </div>

  <div class="sb-body">
    <div class="sb-section-label">历史记录</div>
    <div id="hist-list"><div style="font-size:11px;color:var(--t3);padding:8px">加载中...</div></div>
  </div>

  <!-- 覆盖率卡片 -->
  <div class="sb-footer" id="cov-card" style="display:none">
    <div class="cov-title">静态分析覆盖</div>
    <div class="cov-row">
      <span class="cov-label">已分析行数</span>
      <span class="cov-val" id="cov-lines">—</span>
    </div>
    <div class="cov-track"><div class="cov-bar" id="cov-bar" style="width:0%"></div></div>
    <div class="cov-row">
      <span class="cov-label">已分析函数</span>
      <span class="cov-val" id="cov-funcs">—</span>
    </div>
    <div class="cov-row" style="margin-top:4px">
      <span class="cov-label">分析轮次</span>
      <span class="cov-val" id="cov-sessions">—</span>
    </div>
  </div>
</nav>

<!-- ── MAIN ── -->
<div id="main">
  <!-- TOPBAR -->
  <header class="topbar">
    <div>
      <div class="topbar-title" id="tb-title">软件测试智能体</div>
      <div class="topbar-sub" id="tb-time">—</div>
    </div>
    <div style="margin-left:auto;display:flex;gap:8px;align-items:center">
      <button class="btn-stop" id="btn-stop" onclick="stopTest()">⏹ 停止</button>
      <div class="badge idle" id="badge">
        <span class="dot-pulse" id="dp" style="display:none"></span>
        <span id="badge-txt">待机</span>
      </div>
    </div>
  </header>

  <!-- SPLIT -->
  <div id="split">
    <!-- LOG -->
    <div id="log-pane">
      <div class="pane-bar">
        <span class="pane-pip" style="background:var(--violet)"></span>
        实时日志
      </div>
      <div id="log-scroll">
        <div class="log-line empty">等待测试开始...</div>
      </div>
      <!-- PROGRESS -->
      <div id="prog-wrap">
        <div class="prog-row">
          <span class="prog-lbl" id="prog-lbl">—</span>
          <span class="prog-pct" id="prog-pct">0%</span>
        </div>
        <div class="prog-track"><div class="prog-bar" id="prog-bar"></div></div>
        <div class="prog-stats">
          <div class="stat">
            <div class="stat-d" style="background:var(--green)"></div>
            <span style="color:var(--t2)">通过</span>
            <span id="s-pass" style="color:var(--green);font-weight:600">0</span>
          </div>
          <div class="stat">
            <div class="stat-d" style="background:var(--red)"></div>
            <span style="color:var(--t2)">失败</span>
            <span id="s-fail" style="color:var(--red);font-weight:600">0</span>
          </div>
          <div class="stat">
            <div class="stat-d" style="background:var(--t3)"></div>
            <span style="color:var(--t2)">总计</span>
            <span id="s-total" style="color:var(--t1);font-weight:600">0</span>
          </div>
        </div>
      </div>
    </div>

    <div id="resizer"></div>

    <!-- REPORT -->
    <div id="rpt-pane">
      <div class="pane-bar">
        <span class="pane-pip" style="background:var(--cyan)"></span>
        测试报告
        <span style="margin-left:auto;font-size:10px;color:var(--t3)" id="rpt-name"></span>
      </div>
      <div id="rpt-scroll">
        <div class="rpt-ph">
          <div class="rpt-ph-icon">
            <svg width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24" style="color:var(--t3)">
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/>
              <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
            </svg>
          </div>
          <p style="font-size:13px;color:var(--t3)">运行测试后报告显示在此</p>
          <p style="font-size:11px;color:var(--t3)">或点击左侧历史记录查看</p>
        </div>
      </div>
    </div>
  </div>

  <!-- INPUT -->
  <div id="input-area">
    <div class="cfg-row">
      <div class="cfg-grp" style="max-width:180px">
        <div class="cfg-lbl">API 平台</div>
        <select class="cfg-sel" id="provider" onchange="onProvider()">
          <option value="openrouter">OpenRouter</option>
          <option value="v3">api.v3.cm</option>
        </select>
      </div>
      <div class="cfg-grp" style="max-width:200px">
        <div class="cfg-lbl">模型</div>
        <select class="cfg-sel" id="model-sel"></select>
      </div>
      <div class="cfg-grp" style="flex:2">
        <div class="cfg-lbl">API Key</div>
        <input type="password" class="cfg-inp" id="api-key" placeholder="sk-or-... 或 sk-...">
      </div>
    </div>
    <div class="inp-row">
      <textarea id="prompt" placeholder="描述测试目标（留空使用默认配置：SQLite3 数据库引擎）&#10;例：测试一个 C 语言计算器程序，重点测试边界值和异常输入..."></textarea>
      <button class="btn-run" id="btn-run" onclick="startTest()">
        <svg fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
        运行
      </button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
const io_socket = io();
let running = false, activeHistIdx = -1;

// ── socket events ──────────────────────────────────────────────
io_socket.on('connect', () => { loadHistory(); loadCoverage(); });

io_socket.on('log', d => addLog(d.text, d.type || 'info'));

io_socket.on('progress', d => updateProg(d));

io_socket.on('test_done', d => {
  running = false;
  setStatus(d.success ? 'done' : 'error', d.success ? '完成' : '异常');
  document.getElementById('btn-run').disabled = false;
  document.getElementById('btn-stop').classList.remove('show');
  if (d.report_path) showReport(d.report_path);
  loadHistory();
  loadCoverage();
  toast(d.success ? '✅ 测试完成' : '❌ 测试异常结束');
});

// ── test control ───────────────────────────────────────────────
function startTest() {
  if (running) return;
  const key = document.getElementById('api-key').value.trim();
  if (!key) { toast('⚠️ 请填写 API Key'); return; }

  running = true;
  clearLog();
  setStatus('running', '运行中');
  document.getElementById('btn-run').disabled = true;
  document.getElementById('btn-stop').classList.add('show');
  document.getElementById('prog-wrap').style.display = 'block';
  setRptPlaceholder('运行中，稍候...');
  updateProg({ done:0, total:0, passed:0, failed:0, label:'初始化...' });

  io_socket.emit('start_test', {
    api_key: key,
    provider: document.getElementById('provider').value,
    model: document.getElementById('model-sel').value,
    prompt: document.getElementById('prompt').value.trim(),
  });
}

function stopTest() {
  io_socket.emit('stop_test');
  toast('正在停止...');
}

// ── log ────────────────────────────────────────────────────────
function addLog(text, type) {
  const scr = document.getElementById('log-scroll');
  const ph = scr.querySelector('.log-line.empty');
  if (ph) ph.remove();
  const d = document.createElement('div');
  d.className = 'log-line ' + classOf(text, type);
  d.textContent = text;
  scr.appendChild(d);
  scr.scrollTop = scr.scrollHeight;
}

function classOf(text, type) {
  if (type === 'pass' || text.includes('✅') || /\bPASS\b/.test(text)) return 'ok';
  if (type === 'fail' || text.includes('❌') || /\bFAIL\b|ERROR/.test(text)) return 'fail';
  if (type === 'section' || (text.includes('[Step') || /^={3,}/.test(text.trim()))) return 'sec';
  if (type === 'cmd' || text.includes('$ ') || text.includes('[Terminal]')) return 'cmd';
  if (type === 'stderr' || text.includes('[stderr]')) return 'err';
  if (type === 'stdout' || text.includes('[stdout]')) return 'out';
  if (type === 'muted' || text.includes('[自动确认]')) return 'dim';
  return 'info';
}

function clearLog() {
  document.getElementById('log-scroll').innerHTML = '';
}

// ── progress ───────────────────────────────────────────────────
function updateProg(d) {
  const pct = d.total > 0 ? Math.round(d.done / d.total * 100) : 0;
  document.getElementById('prog-bar').style.width = pct + '%';
  document.getElementById('prog-pct').textContent = pct + '%';
  document.getElementById('prog-lbl').textContent = d.label || (d.done + '/' + d.total);
  document.getElementById('s-pass').textContent = d.passed || 0;
  document.getElementById('s-fail').textContent = d.failed || 0;
  document.getElementById('s-total').textContent = d.total || 0;
}

// ── status badge ───────────────────────────────────────────────
function setStatus(state, txt) {
  const b = document.getElementById('badge');
  const dp = document.getElementById('dp');
  b.className = 'badge ' + state;
  document.getElementById('badge-txt').textContent = txt;
  dp.style.display = state === 'running' ? 'block' : 'none';
}

// ── report ─────────────────────────────────────────────────────
function showReport(path) {
  fetch('/report?path=' + encodeURIComponent(path))
    .then(r => r.json()).then(d => {
      if (d.content) {
        document.getElementById('rpt-scroll').innerHTML = marked.parse(d.content);
        document.getElementById('rpt-name').textContent =
          path.split(/[/\\]/).pop().replace('.md','');
      }
    });
}

function setRptPlaceholder(msg) {
  document.getElementById('rpt-scroll').innerHTML =
    '<div class="rpt-ph"><p style="font-size:13px;color:var(--t3)">' + msg + '</p></div>';
}

// ── history ────────────────────────────────────────────────────
function loadHistory() {
  fetch('/history').then(r => r.json()).then(d => renderHistory(d.reports));
}

function renderHistory(reports) {
  const list = document.getElementById('hist-list');
  list.innerHTML = '';
  if (!reports.length) {
    list.innerHTML = '<div style="font-size:11px;color:var(--t3);padding:8px">暂无历史记录</div>';
    return;
  }
  reports.forEach((r, i) => {
    const el = document.createElement('div');
    el.className = 'hist-item' + (i === 0 ? ' active' : '');
    el.dataset.path = r.md_path;
    el.dataset.idx = i;
    const pct = parseFloat(r.pass_rate) || 0;
    const ok = pct >= 100;
    el.innerHTML =
      '<span class="hist-icon ' + (ok ? 'ok' : 'ng') + '"></span>' +
      '<div class="hist-info">' +
        '<div class="hist-name">' + escHtml(r.project || '报告') + '</div>' +
        '<div class="hist-meta">' + r.time + ' · ' + r.pass_rate + '</div>' +
      '</div>';
    el.onclick = () => onHistClick(el, r, i);
    list.appendChild(el);
  });
  // 自动加载最新
  if (reports.length && !running) showReport(reports[0].md_path);
}

function onHistClick(el, r, i) {
  if (activeHistIdx === i) {
    // 再次点击 → 取消，跳到最新/运行中状态
    activeHistIdx = -1;
    document.querySelectorAll('.hist-item').forEach((x,j) => {
      x.classList.toggle('active', j === 0);
    });
    if (running) {
      setRptPlaceholder('测试运行中，报告完成后显示...');
    } else {
      const first = document.querySelector('.hist-item');
      if (first) showReport(first.dataset.path);
    }
    return;
  }
  activeHistIdx = i;
  document.querySelectorAll('.hist-item').forEach(x => x.classList.remove('active'));
  el.classList.add('active');
  showReport(r.md_path);
}

// ── coverage ───────────────────────────────────────────────────
function loadCoverage() {
  fetch('/coverage').then(r => r.json()).then(d => {
    if (!d.found) return;
    document.getElementById('cov-card').style.display = 'block';
    document.getElementById('cov-lines').textContent = d.lines.toLocaleString();
    document.getElementById('cov-funcs').textContent = d.funcs;
    document.getElementById('cov-sessions').textContent = d.sessions;
    const pct = Math.min(100, d.lines / 2300 * 100); // ~230k lines total → per snippet
    document.getElementById('cov-bar').style.width = pct + '%';
  }).catch(() => {});
}

// ── sidebar ────────────────────────────────────────────────────
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('collapsed');
}

// ── provider ───────────────────────────────────────────────────
function onProvider() {
  const p = document.getElementById('provider').value;
  const sel = document.getElementById('model-sel');
  sel.innerHTML = '';
  const opts = p === 'openrouter'
    ? [['anthropic/claude-sonnet-4-5','Claude Sonnet 4.5'],
       ['anthropic/claude-3-5-haiku','Claude 3.5 Haiku'],
       ['anthropic/claude-opus-4','Claude Opus 4']]
    : [['gpt-4o','GPT-4o'],['gpt-4o-mini','GPT-4o mini'],
       ['claude-sonnet-4-5','Claude Sonnet'],
       ['qwen3-vl-plus','Qwen3-VL-Plus'],
       ['deepseek-chat','DeepSeek Chat']];
  opts.forEach(([v,l]) => {
    const o = document.createElement('option');
    o.value = v; o.textContent = l;
    sel.appendChild(o);
  });
}
onProvider();

// ── time ───────────────────────────────────────────────────────
function tick() {
  document.getElementById('tb-time').textContent =
    new Date().toLocaleString('zh-CN',{hour12:false});
}
setInterval(tick, 1000); tick();

// ── toast ──────────────────────────────────────────────────────
let toastTimer;
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 3000);
}

// ── resizer ────────────────────────────────────────────────────
(()=>{
  const r = document.getElementById('resizer');
  const rp = document.getElementById('rpt-pane');
  let drag=false, x0=0, w0=0;
  r.addEventListener('mousedown', e => {
    drag=true; x0=e.clientX; w0=rp.offsetWidth;
    r.classList.add('active');
    document.body.style.cssText='cursor:col-resize;user-select:none';
  });
  document.addEventListener('mousemove', e => {
    if(!drag) return;
    const nw = Math.max(280, Math.min(w0+(x0-e.clientX), window.innerWidth*.72));
    rp.style.width = nw+'px';
  });
  document.addEventListener('mouseup', ()=>{
    if(!drag) return; drag=false;
    r.classList.remove('active');
    document.body.style.cssText='';
  });
})();

function escHtml(s){
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
</script>
</body>
</html>'''


# ── Flask 路由 ──────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/history')
def history():
    out = os.path.join(os.path.dirname(__file__), 'output')
    os.makedirs(out, exist_ok=True)
    mds = sorted(glob.glob(os.path.join(out, 'report_*.md')), reverse=True)
    reports = []
    for md in mds[:20]:
        base = os.path.splitext(md)[0]
        ts = os.path.basename(md).replace('report_','').replace('.md','')
        try:
            dt = datetime.strptime(ts, '%Y%m%d_%H%M%S')
            tstr = dt.strftime('%m/%d %H:%M')
        except:
            tstr = ts
        project, pass_rate = 'SQLite3', '?%'
        jp = base + '.json'
        if os.path.exists(jp):
            try:
                with open(jp, encoding='utf-8') as f:
                    d = json.load(f)
                project = d.get('project', project)
                pass_rate = d.get('summary', {}).get('pass_rate', pass_rate)
            except:
                pass
        reports.append({'md_path': md, 'time': tstr, 'project': project, 'pass_rate': pass_rate})
    return jsonify({'reports': reports})


@app.route('/report')
def report():
    path = request.args.get('path', '')
    if not path or not os.path.exists(path):
        return jsonify({'error': 'not found', 'content': ''})
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify({'content': f.read()})
    except Exception as e:
        return jsonify({'error': str(e), 'content': ''})


@app.route('/coverage')
def coverage():
    hist = os.path.join(os.path.dirname(__file__), 'static_analysis_history.json')
    if not os.path.exists(hist):
        return jsonify({'found': False})
    try:
        with open(hist, encoding='utf-8') as f:
            d = json.load(f)
        ranges = d.get('analyzed_ranges', [])
        funcs = d.get('analyzed_functions', [])
        sessions = d.get('total_sessions', 0)
        total_lines = sum(max(0, e - s) for s, e in ranges)
        return jsonify({
            'found': True,
            'lines': total_lines,
            'funcs': len(funcs),
            'sessions': sessions,
        })
    except:
        return jsonify({'found': False})


# ── SocketIO ────────────────────────────────────────────────────────────

@socketio.on('start_test')
def on_start(data):
    global test_running, test_thread
    if test_running:
        emit('log', {'text': '已有测试在运行', 'type': 'fail'})
        return
    os.environ['ANTHROPIC_API_KEY'] = data.get('api_key', '')
    os.environ['LLM_PROVIDER'] = data.get('provider', 'openrouter')
    m = data.get('model', '')
    if m:
        if data.get('provider') == 'openrouter':
            os.environ['OPENROUTER_MODEL'] = m
        else:
            os.environ['V3_MODEL'] = m
    test_running = True
    sid = request.sid
    test_thread = threading.Thread(
        target=_run, args=(sid, data.get('prompt', ''), data.get('framework')),
        daemon=True
    )
    test_thread.start()


@socketio.on('stop_test')
def on_stop():
    global test_running
    test_running = False
    socketio.emit('log', {'text': '⏹ 已停止', 'type': 'fail'})


def _run(sid, prompt, custom_fw):
    global test_running
    import builtins, importlib, traceback

    def push(text, t='info'):
        if not str(text).strip(): return
        socketio.emit('log', {'text': str(text), 'type': t}, room=sid)

    orig_print = builtins.print
    orig_input = builtins.input

    def p(*a, **kw):
        text = ' '.join(str(x) for x in a)
        push(text)
        orig_print(*a, **kw)

    def inp(prompt_text=''):
        push(f'[自动确认] {prompt_text} → y', 'muted')
        return 'y'

    builtins.print = p
    builtins.input = inp
    rpt_path, ok = None, False

    try:
        import config
        importlib.reload(config)
        config.ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
        config.LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'openrouter')

        from agents.env_agent import EnvAgent
        from agents.static_analysis_agent import StaticAnalysisAgent
        from agents.planner_agent import PlannerAgent
        from agents.executor_agent import ExecutorAgent, TestReport
        from agents.refinement_agent import RefinementAgent
        from core.reporter import Reporter, append_static_analysis
        from core.reporter import append_refined_results, append_overall_summary, merge_reports

        root = os.path.dirname(os.path.abspath(__file__))
        fw = {**DEFAULT_FRAMEWORK, **(custom_fw or {})}
        if prompt:
            fw['test_goals'] = [prompt] + fw.get('test_goals', [])

        push('=' * 48, 'sec')
        push('  Testing Agent  ·  Web Mode', 'sec')
        push('=' * 48, 'sec')

        # EnvAgent
        push('\n[Step 1/6] 探测系统环境...', 'sec')
        framework = EnvAgent(workdir=root).detect_and_fix(fw)

        if not test_running: return

        # 编译
        if framework.get('compile_cmd'):
            push('\n[Step 2/6] 编译...', 'sec')
            from core.terminal import TerminalExecutor
            res = TerminalExecutor(workdir=root).run(framework['compile_cmd'])
            push(('✅ 编译成功' if res.success else '❌ 编译失败: ' + res.stderr),
                 'pass' if res.success else 'fail')

        # 静态分析
        push('\n[Step 3/6] 静态代码分析...', 'sec')
        static_rpt = StaticAnalysisAgent(workdir=root).analyze(framework)

        if not test_running: return

        # Plan
        push('\n[Step 4/6] 生成测试任务...', 'sec')
        tasks = PlannerAgent().plan(framework, static_report=static_rpt)
        total = len(tasks)
        socketio.emit('progress', {'done':0,'total':total,'passed':0,'failed':0,'label':f'准备 {total} 个任务'}, room=sid)

        # Execute
        push(f'\n[Step 5/6] 执行测试（{total} 个）...', 'sec')
        executor = ExecutorAgent(workdir=root)
        report = TestReport(project_name=framework['project_name'], total=total)
        executor._tool_path = framework.get('_tool_path', '')

        for i, task in enumerate(tasks):
            if not test_running: break
            push(f'\n  [{i+1}/{total}] {task.task_id}: {task.description}')
            res = executor._run_task(task)
            report.results.append(res)
            if res.passed:
                report.passed += 1; push('  ✅ PASS', 'pass')
            elif res.verdict == 'ERROR':
                report.errors += 1; push('  ⚠️ ERROR', 'fail')
            else:
                report.failed += 1; push('  ❌ FAIL', 'fail')
            socketio.emit('progress', {
                'done':i+1,'total':total,
                'passed':report.passed,'failed':report.failed,
                'label':f'{i+1}/{total} · {task.task_id}'
            }, room=sid)

        # Refine
        push('\n[Step 6/6] 精化测试...', 'sec')
        all_rpts, all_tasks, bugs = [], [], []
        cur = report
        refiner = RefinementAgent()
        tested = {cr.command for r in report.results for cr in r.cmd_results}

        for rnd in range(1, 3):
            if not test_running: break
            fails = [r for r in cur.results if r.verdict in ('FAIL','ERROR')]
            if not fails: push(f'第{rnd}轮无失败，停止精化', 'pass'); break
            push(f'\n精化第 {rnd} 轮（{len(fails)} 个失败）...', 'sec')
            rtasks = refiner.refine(cur, framework)
            ntasks = [t for t in rtasks if not any(c in tested for c in t.commands)]
            if not ntasks: push('无新任务', 'dim'); break
            rr = TestReport(project_name=framework['project_name']+f'(精化{rnd})', total=len(ntasks))
            executor._tool_path = framework.get('_tool_path', '')
            for i, t in enumerate(ntasks):
                if not test_running: break
                push(f'  [{i+1}/{len(ntasks)}] {t.task_id}: {t.description[:50]}')
                res = executor._run_task(t)
                rr.results.append(res)
                if res.passed: rr.passed += 1; push('  ✅ PASS','pass')
                else:
                    rr.failed += 1; push('  ❌ FAIL','fail')
                    al = (res.analysis or '').lower()
                    if not any(k in al for k in ['未发现缺陷','符合预期','正常行为']):
                        bugs.append(res)
                for c in t.commands: tested.add(c)
            all_rpts.append(rr); all_tasks.extend(ntasks); cur = rr

        final = merge_reports(report, all_rpts) if all_rpts else report
        rep = Reporter()
        jp, mp = rep.save(final)
        append_static_analysis(mp, static_rpt)
        for i, r in enumerate(all_rpts):
            append_refined_results(mp, r, all_tasks, round_num=i+1, confirmed_bugs=bugs)
        append_overall_summary(mp, final, static_rpt, confirmed_bugs=bugs)

        rpt_path = mp; ok = True
        push('\n' + '=' * 48, 'sec')
        push(f'  完成！通过率 {final.pass_rate:.1f}%', 'pass')
        push('=' * 48, 'sec')

    except Exception as e:
        push(f'异常: {e}', 'fail')
        push(traceback.format_exc(), 'fail')
    finally:
        builtins.print = orig_print
        builtins.input = orig_input
        test_running = False
        socketio.emit('test_done', {'success': ok, 'report_path': rpt_path}, room=sid)


if __name__ == '__main__':
    print('=' * 50)
    print('  软件测试智能体 Web 界面')
    print('  http://localhost:5000')
    print('=' * 50)
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)