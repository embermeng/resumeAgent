"""
Streamlit Web UI - ResumeAgent
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
from src.config import get_config
from src.agent.graph import ResumeAgent

# 页面配置
st.set_page_config(
    page_title="ResumeAgent - 简历生成助手",
    page_icon="📄",
    layout="wide",
)

# 初始化session state
if "messages" not in st.session_state:
    st.session_state.messages = []
if "agent" not in st.session_state:
    st.session_state.agent = None


def get_agent():
    """获取或创建Agent实例"""
    if st.session_state.agent is None:
        config = get_config()
        st.session_state.agent = ResumeAgent(config=config)
    return st.session_state.agent


# 侧边栏
with st.sidebar:
    st.title("📄 ResumeAgent")
    st.markdown("---")
    st.markdown("### 功能")
    st.markdown("- 💬 技术知识问答")
    st.markdown("- 📋 项目经历查询")
    st.markdown("- 📝 简历生成与优化")
    st.markdown("---")
    st.markdown("### 使用方式")
    st.markdown("直接在聊天框输入问题即可：")
    st.markdown("- 问技术问题 → 快速回答模式")
    st.markdown("- 说'生成简历' → 深度思考模式")
    st.markdown("---")
    if st.button("🗑️ 清空对话"):
        st.session_state.messages = []
        st.rerun()

# 主聊天区域
st.title("ResumeAgent - 智能简历生成助手")

# 显示历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("intent"):
            st.caption(f"模式: {msg['intent']}")

# 用户输入
if prompt := st.chat_input("输入你的问题..."):
    # 添加用户消息
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Agent处理
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            try:
                agent = get_agent()
                result = agent.run(prompt)

                response = result.get("final_response", "抱歉，处理出错。")
                intent = result.get("intent", "unknown")
                step = result.get("step", "")

                st.markdown(response)

                # 显示意图信息
                intent_labels = {
                    "quick_response": "⚡ 快速回答",
                    "deep_thinking": "🧠 深度思考",
                    "chitchat": "💬 闲聊",
                }
                st.caption(f"模式: {intent_labels.get(intent, intent)} | 步骤: {step}")

                # 如果有简历草稿，提供下载
                if result.get("resume_final"):
                    st.download_button(
                        "📥 下载简历 (Markdown)",
                        result["resume_final"],
                        file_name="resume.md",
                        mime="text/markdown",
                    )

                # 保存消息
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response,
                    "intent": intent_labels.get(intent, intent),
                })

            except Exception as e:
                error_msg = f"处理出错: {e}"
                st.error(error_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": error_msg,
                })
