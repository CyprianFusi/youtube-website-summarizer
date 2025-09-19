from dotenv import load_dotenv, find_dotenv
import streamlit as st
from langchain_groq import ChatGroq
from langchain_community.utilities import ArxivAPIWrapper, WikipediaAPIWrapper
from langchain_community.tools import ArxivQueryRun, WikipediaQueryRun
from langchain.agents import initialize_agent, AgentType
from langchain_community.callbacks.streamlit import StreamlitCallbackHandler

_ = load_dotenv(find_dotenv())

## ArXiv and Wikipedia Tools
arxiv_wrapper = ArxivAPIWrapper()
arxiv = ArxivQueryRun(api_wrapper=arxiv_wrapper)

api_wrapper = WikipediaAPIWrapper()
wiki = WikipediaQueryRun(api_wrapper=api_wrapper)

st.title("🔎 Binati Multi-Agents Research")
st.markdown(":blue[**Ask questions and get answers with ArXiv and Wikipedia tools**]")
st.info("📝 **Note**: This app works with ArXiv papers and Wikipedia. For YouTube videos, please describe the content instead of pasting URLs.")

## Sidebar for settings
st.sidebar.title("Settings")
api_key = st.sidebar.text_input("Enter your Groq API Key:", type="password")

if api_key:
    llm = ChatGroq(groq_api_key=api_key, model_name="llama-3.1-8b-instant", streaming=True)

    if "messages" not in st.session_state:
        st.session_state["messages"] = [
            {"role": "assistant", "content": "Hi, I'm a chatbot who can search ArXiv papers and Wikipedia articles. How can I help you with your research?"}
        ]

    for msg in st.session_state.messages:
        st.chat_message(msg["role"]).write(msg['content'])

    if prompt := st.chat_input(placeholder="What is machine learning?"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.chat_message("user").write(prompt)

        tools = [arxiv, wiki]

        search_agent = initialize_agent(
            tools, 
            llm, 
            agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, 
            handling_parsing_errors=True,
            verbose=True
        )

        # using `StreamlitCallbackHandler` to display the thoughts and actions of an agent in an interactive Streamlit app
        with st.chat_message("assistant"):
            st_cb = StreamlitCallbackHandler(st.container(), expand_new_thoughts=False)
            try:
                # Pass only the current prompt, not all messages
                response = search_agent.run(prompt, callbacks=[st_cb])
                st.session_state.messages.append({'role': 'assistant', "content": response})
                st.write(response)
            except Exception as e:
                error_msg = f"I encountered an error while processing your request. Please try rephrasing your question or ask about ArXiv papers or Wikipedia topics. Error: {str(e)}"
                st.error(error_msg)
                st.session_state.messages.append({'role': 'assistant', "content": error_msg})
else:
    st.warning("Please enter the Groq API Key")