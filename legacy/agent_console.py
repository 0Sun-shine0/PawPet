"""小爪 AI 控制台：视觉状态、AI 问答、MCP 配置与受控动作入口。"""
from __future__ import annotations
import tkinter as tk
from tkinter import messagebox
import json, os
from ai_client import OpenAIClient
from screen_vision import ScreenVision
from desktop_actions import DesktopActions
from mcp_client import MCPStdioClient

class AgentConsole:
    def __init__(self, parent):
        self.win=tk.Toplevel(parent); self.win.title("小爪 AI 控制台"); self.win.geometry("620x560"); self.win.configure(bg="#201c2b"); self.win.attributes("-topmost", True)
        self.vision=ScreenVision(); self.actions=DesktopActions(); self.ai=OpenAIClient(); self.last_png=None
        tk.Label(self.win,text="AI 控制台",bg="#312a44",fg="#ffe7c4",font=("Microsoft YaHei UI",18,"bold"),anchor="w").pack(fill="x",ipady=12,padx=18)
        self.status=tk.Label(self.win,text=self._status(),bg="#201c2b",fg="#bca9ed",justify="left",anchor="w"); self.status.pack(fill="x",padx=18,pady=12)
        row=tk.Frame(self.win,bg="#201c2b"); row.pack(fill="x",padx=18)
        for text,cmd in (("捕获屏幕",self.capture),("分析当前屏幕",self.analyze),("连接 MCP",self.connect_mcp),("清空",self.clear)):
            tk.Button(row,text=text,command=cmd,bg="#554869",fg="white",relief="flat",padx=12).pack(side="left",padx=(0,8))
        self.output=tk.Text(self.win,height=16,bg="#211c2e",fg="#ede5f6",insertbackground="white",relief="flat",wrap="word"); self.output.pack(fill="both",expand=True,padx=18,pady=12)
        entry=tk.Frame(self.win,bg="#201c2b"); entry.pack(fill="x",padx=18,pady=(0,14))
        self.prompt=tk.Entry(entry,bg="#211c2e",fg="white",insertbackground="white",relief="flat"); self.prompt.pack(side="left",fill="x",expand=True,ipady=8); self.prompt.bind("<Return>",lambda e:self.ask())
        tk.Button(entry,text="发送给 AI",command=self.ask,bg="#9377ce",fg="white",relief="flat").pack(side="left",padx=(8,0),ipady=4)
    def _status(self): return f"{self.vision.status()}\n{self.actions.status()}\nOpenAI：{'已配置' if self.ai.configured else '未配置（设置 OPENAI_API_KEY 后可用）'}"
    def write(self,text): self.output.insert("end",text+"\n"); self.output.see("end")
    def capture(self):
        r=self.vision.capture(); self.last_png=r.image_png; self.write(r.message + (f"，平均颜色 BGR={r.mean_bgr}" if r.ok else ""))
    def analyze(self):
        if not self.last_png: self.capture()
        if not self.last_png: return
        r=self.ai.ask("请描述当前屏幕中最重要的可见信息，并给出下一步建议。不要执行任何动作。", self.vision.as_data_url(self.last_png), "你是小爪助手的视觉分析模块。只观察，不臆测，不执行动作。")
        self.write(r.get("text") if r.get("ok") else r.get("error"))
    def ask(self):
        text=self.prompt.get().strip(); self.prompt.delete(0,"end")
        if not text:return
        r=self.ai.ask(text, self.vision.as_data_url(self.last_png) if self.last_png else None, "你是桌面助手。提出动作建议时先说明风险；不要自行执行删除、发送、登录、支付等高影响操作。")
        self.write(("AI：" + r.get("text")) if r.get("ok") else r.get("error"))
    def clear(self): self.output.delete("1.0","end")
    def connect_mcp(self):
        path=os.path.join(os.path.dirname(os.path.abspath(__file__)),"mcp_servers.json")
        try:
            cfg=json.load(open(path,encoding="utf-8"))
            servers=[s for s in cfg.get("servers",[]) if s.get("enabled")]
            if not servers: self.write("MCP：配置文件中没有启用的 server。编辑 mcp_servers.json 后重试。"); return
            for spec in servers:
                client=MCPStdioClient(spec["command"]); init=client.start(); tools=client.list_tools()
                names=[x.get("name") for x in tools.get("result",{}).get("tools",[])]
                self.write(f"MCP {spec.get('name','unnamed')} 已连接，可用工具：{', '.join(names) or '无'}")
        except Exception as exc: self.write("MCP 连接失败："+str(exc))
