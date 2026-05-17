"""大模型API客户端 —— 接入阿里云千问"""
import os
from openai import OpenAI
from dotenv import load_dotenv

# 加载 .env 文件里的环境变量
load_dotenv()

# 创建OpenAI兼容客户端，指向千问的地址
client = OpenAI(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

# 千问的模型名称
MODEL_NAME = "qwen-plus"


async def call_llm(system_prompt: str, messages: list) -> str:
    """
    调用千问API，返回AI回复
    
    参数:
        system_prompt: 系统提示词（定义了AI的引导策略）
        messages: 完整对话历史 [{role, content}, ...]
    
    返回:
        AI的回复文本
    """
    try:
        # 拼出 API 需要的 messages 格式
        api_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            api_messages.append({"role": msg["role"] if isinstance(msg, dict) else msg.role,
                                 "content": msg["content"] if isinstance(msg, dict) else msg.content})

        # 调用千问API（同步调用，因为openai库不支持async）
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=api_messages,
            temperature=0.7,   # 控制随机性
            max_tokens=500,     # 限制回复长度
        )
        
        # 提取回复内容
        return response.choices[0].message.content
        
    except Exception as e:
        # 如果调用失败，返回错误信息
        return f"AI调用失败: {str(e)}"