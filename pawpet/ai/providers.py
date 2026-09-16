"""模型服务商预设 —— 泛用户「第一次配模型」的向导数据。

为什么需要这个
--------------
泛用户卡在第一步：他连「OpenAI 兼容接口」是什么都不知道，更不知道
去哪买 key、填哪个地址、选哪个模型。原来界面上只有两个空输入框
（接口地址 / 模型名），等于让他自己查文档。

所以这里把「人话名字 → 注册页链接 → 接口地址 → 常用模型」打包好，
界面上点一下就自动填好，再点一下就跳到注册页。

设计上的几条规矩
----------------
* **链接要能直接跳到「拿 key」那一步**，不要只给官网首页 ——
  用户点进去还要自己找「控制台 → API Keys → 创建」，一半人会放弃。
  所以每家都给最深的那个能直达的页面。
* **价格不写死在代码里。** 各家调价很频繁，写死了三个月后就是错的，
  反而误导用户。所以只给「要不要先充值」这类稳定信息 + 价格页链接，
  具体数字让他自己看一眼。
* **本地模型也要有位置**：不想花钱、或者数据敏感的用户，Ollama 是不出网的
  选项，全程不需要 key。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 分组：国内直连 / 国外需网络 / 本地
GROUP_CN = "cn"
GROUP_GLOBAL = "global"
GROUP_LOCAL = "local"

GROUP_LABELS = {
    GROUP_CN: "国内（直连，推荐）",
    GROUP_GLOBAL: "国外（需要能访问外网）",
    GROUP_LOCAL: "本地跑（不用花钱、不出网）",
}


@dataclass(frozen=True)
class Provider:
    """一家模型服务商。"""

    key: str
    name: str
    group: str
    # 注册/拿 key 的直达链接。空串表示不需要（本地模型）
    signup_url: str = ""
    # 附带的一两个参考链接：价格、文档
    links: tuple[tuple[str, str], ...] = ()
    base_url: str = ""
    model: str = ""
    # 有没有免费额度 / 要不要先充值 —— 这类信息比对价格稳定
    free_hint: str = ""
    tip: str = ""
    # 本地模型不需要 key
    needs_key: bool = True

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "group": self.group,
            "groupLabel": GROUP_LABELS.get(self.group, self.group),
            "signupUrl": self.signup_url,
            "links": [{"label": label, "url": url} for label, url in self.links],
            "baseUrl": self.base_url,
            "model": self.model,
            "freeHint": self.free_hint,
            "tip": self.tip,
            "needsKey": self.needs_key,
            "hasSignup": bool(self.signup_url),
        }


# ==========================================================================
#  清单
# ==========================================================================
# 链接都指向「能直接拿到 key 或看到价格」的那一页，不是官网首页。
# 价格数字一律不写死（各家调价频繁），只写「要不要先充值」。
PROVIDERS: tuple[Provider, ...] = (
    # ------------------------------------------------------------ 国内
    Provider(
        key="deepseek",
        name="DeepSeek（深度求索）",
        group=GROUP_CN,
        # 直达「创建 API Key」那一页，不是官网首页 ——
        # 用户点进去还要自己找「控制台 → API Keys」，一半人会放弃。
        signup_url="https://platform.deepseek.com/api_keys",
        links=(
            ("看价格", "https://api-docs.deepseek.com/zh-cn/quick_start/pricing"),
            ("接入文档", "https://api-docs.deepseek.com/zh-cn/"),
        ),
        # 官方文档给的 base_url 是 https://api.deepseek.com，
        # 这里带 /v1 是因为客户端走 OpenAI 兼容路径（/chat/completions）。
        # 两种写法 DeepSeek 都接受。
        base_url="https://api.deepseek.com/v1",
        model="deepseek-flash",
        free_hint="手机号注册即可；按用量付费，单价在同类里偏低",
        tip="国内直连、速度快。默认就是它，不用改。",
    ),
    Provider(
        key="moonshot",
        name="Kimi（月之暗面）",
        group=GROUP_CN,
        signup_url="https://platform.moonshot.cn/console/api-keys",
        links=(
            ("看价格", "https://platform.moonshot.cn/docs/pricing/chat"),
        ),
        base_url="https://api.moonshot.cn/v1",
        model="moonshot-v1-8k",
        free_hint="注册有试用额度，用完要充值",
        tip="国内直连。上下文长，适合读长文档。",
    ),
    Provider(
        key="zhipu",
        name="智谱 GLM",
        group=GROUP_CN,
        signup_url="https://open.bigmodel.cn/usercenter/apikeys",
        links=(
            ("看价格", "https://open.bigmodel.cn/pricing"),
        ),
        base_url="https://open.bigmodel.cn/api/paas/v4",
        model="glm-4-flash",
        free_hint="glm-4-flash 有免费档，适合先试",
        tip="国内直连。想先零成本试一下就用它。",
    ),
    Provider(
        key="dashscope",
        name="通义千问（阿里云百炼）",
        group=GROUP_CN,
        signup_url="https://bailian.console.aliyun.com/",
        links=(
            ("看价格", "https://help.aliyun.com/zh/model-studio/models"),
        ),
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="qwen-plus",
        free_hint="新用户各模型有免费额度",
        tip="国内直连。阿里云账号直接开通。",
    ),
    # ------------------------------------------------------------ 国外
    Provider(
        key="openai",
        name="OpenAI（ChatGPT 同一家）",
        group=GROUP_GLOBAL,
        signup_url="https://platform.openai.com/api-keys",
        links=(
            ("看价格", "https://openai.com/api/pricing/"),
        ),
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
        free_hint="要先绑信用卡充值，没有免费额度",
        tip="国内需要能访问外网。gpt-4o-mini 便宜且够用。",
    ),
    Provider(
        key="openrouter",
        name="OpenRouter（一个 key 用多家模型）",
        group=GROUP_GLOBAL,
        signup_url="https://openrouter.ai/keys",
        links=(
            ("看价格", "https://openrouter.ai/models"),
        ),
        base_url="https://openrouter.ai/api/v1",
        model="deepseek/deepseek-chat",
        free_hint="部分模型标了 :free 可以白用（有限速）",
        tip="一个 key 切换几十家的模型，懒得各家注册就用它。",
    ),
    # ------------------------------------------------------------ 本地
    Provider(
        key="ollama",
        name="本地模型（Ollama）",
        group=GROUP_LOCAL,
        signup_url="https://ollama.com/download",
        links=(
            ("看安装说明", "https://github.com/ollama/ollama/blob/main/README.md"),
        ),
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:7b",
        free_hint="完全免费，全程不出网",
        tip="要先装 Ollama 并拉一个模型。数据敏感、或者不想花钱时用它。",
        needs_key=False,
    ),
)


def all_providers() -> list[dict]:
    return [item.as_dict() for item in PROVIDERS]


# 默认推荐的这一家。泛用户点开就该看到一个「去哪拿 Key」的按钮，
# 而不是先做一道七选一的选择题。
PRIMARY_KEY = "deepseek"


def primary() -> dict:
    """默认推的那家（DeepSeek）。"""
    item = find(PRIMARY_KEY)
    return item.as_dict() if item else {}


def alternatives() -> list[dict]:
    """除默认之外的其他家。默认那家单独展示，不混在列表里。"""
    return [item.as_dict() for item in PROVIDERS if item.key != PRIMARY_KEY]


def groups() -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for item in PROVIDERS:
        buckets.setdefault(item.group, []).append(item.as_dict())
    return [
        {
            "key": group,
            "label": GROUP_LABELS[group],
            "items": buckets[group],
        }
        for group in (GROUP_CN, GROUP_GLOBAL, GROUP_LOCAL)
        if buckets.get(group)
    ]


def find(key: str) -> Provider | None:
    for item in PROVIDERS:
        if item.key == key:
            return item
    return None


def guess_key_help(base_url: str) -> tuple[str, str]:
    """根据用户已经填的接口地址，猜出「去哪拿 key」的链接。

    泛用户可能已经手填了地址（比如从同事那抄来的），这时界面上还是要
    给一个「去哪拿 key」的入口 —— 按域名前缀匹配到对应那家。
    """
    host = (base_url or "").lower()
    for item in PROVIDERS:
        if not item.signup_url or not item.base_url:
            continue
        # 只比域名，忽略协议和路径
        domain = item.base_url.split("//")[-1].split("/")[0]
        if domain and domain in host:
            return item.name, item.signup_url
    return "", ""
