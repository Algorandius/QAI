/* QAI 固定模型目录（2026-10）
   能力标识针对模型 API 原生能力；QAI 自身另有 AI_WEB_TOOL 搜索工具。
   web = 原生联网；video = 原生视频理解；free = 免费；local = 本地。 */
window.QAI_MODELS = {
  version: 2,
  providers: [
    {
      id: "zhipu", name: "智谱 GLM", base: "https://open.bigmodel.cn/api/paas/v4/",
      hint: "智谱开放平台，OpenAI 兼容；GLM-4.7-Flash 免费",
      models: [
        {
          id: "glm-4.7-flash", name: "GLM-4.7-Flash（免费）",
          desc: "30B 免费 · 200K · 编程/工具",
          tools: true, reasoning: true, free: true
        },
        {
          id: "glm-5.3-flash", name: "GLM-5.3-Flash",
          desc: "原生图/视频 · 1M · 低成本",
          image: true, video: true, tools: true, reasoning: true
        },
        {
          id: "glm-5.3", name: "GLM-5.3",
          desc: "旗舰编程/Agent · 1M · 强思考",
          tools: true, reasoning: true
        }
      ]
    },
    {
      id: "deepseek", name: "DeepSeek", base: "https://api.deepseek.com/v1",
      hint: "DeepSeek 官方 API，OpenAI 兼容；V4.1 Flash 支持识图",
      models: [
        {
          id: "deepseek-flash", name: "DeepSeek-V4.1-Flash",
          desc: "高性价比 · 1M · 识图/工具",
          image: true, tools: true, reasoning: true
        },
        {
          id: "deepseek-v4-pro", name: "DeepSeek-V4-Pro",
          desc: "旗舰推理 · 1M · 工具",
          tools: true, reasoning: true
        }
      ]
    },
    {
      id: "qwen", name: "阿里通义千问", base: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      hint: "DashScope OpenAI 兼容模式",
      models: [
        {
          id: "qwen3.8-flash", name: "qwen3.8-flash",
          desc: "全模态图/视频 · 1M · 联网",
          image: true, video: true, web: true, tools: true, reasoning: true
        }
      ]
    },
    {
      id: "kimi", name: "Kimi", base: "https://api.moonshot.cn/v1",
      hint: "Kimi 开放平台，OpenAI 兼容",
      models: [
        {
          id: "kimi-k3", name: "kimi-k3",
          desc: "2.8T 旗舰 · 1M · 视觉/编程",
          image: true, web: true, tools: true, reasoning: true
        }
      ]
    },
    {
      id: "hunyuan", name: "混元 HY", base: "https://tokenhub.tencentmaas.com/v1",
      hint: "腾讯混元 Hy3 / Hy4；Hy4 走国际站，二者共用混元 Key",
      models: [
        {
          id: "hy3-preview", name: "Hy3",
          desc: "295B MoE · 256K · Agent",
          base: "https://tokenhub.tencentmaas.com/v1",
          tools: true, reasoning: true
        },
        {
          id: "hy4-preview", name: "Hy4 preview",
          desc: "770B Agent/Coding · 1M · 识图 · 预览",
          base: "https://tokenhub-intl.tencentcloudmaas.com/v1",
          image: true, tools: true, reasoning: true
        }
      ]
    }
  ]
};
