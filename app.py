import validators
import traceback
import yt_dlp
import requests
import re
import streamlit as st
from langchain.prompts import PromptTemplate
from langchain_groq import ChatGroq
from langchain.schema import Document
from urllib.parse import urlparse, parse_qs
from langchain.chains.summarize import load_summarize_chain
from langchain_community.document_loaders import UnstructuredURLLoader
from youtube_transcript_api import YouTubeTranscriptApi

## Streamlit APP
st.set_page_config(page_title="LangChain: Summarize Text From YouTubeT or Website", page_icon="🦜")
st.title("Summarize Text From YouTube or Website")
st.subheader('Summarize URL')

## Get the Groq API Key and url(YT or website) to be summarized
with st.sidebar:
    groq_api_key = st.text_input("Groq API Key", value="", type="password")

generic_url = st.text_input("URL", label_visibility="collapsed")

prompt_template = """
Provide a summary of the following content in not more than 300 words:
Content:{text}

"""
prompt = PromptTemplate(template=prompt_template, input_variables=["text"])

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36"
}

def is_youtube_url(url):
    """Check if URL is a YouTube URL"""
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=[\w-]+',
        r'(?:https?://)?(?:www\.)?youtu\.be/[\w-]+',
        r'(?:https?://)?(?:www\.)?youtube\.com/embed/[\w-]+',
        r'(?:https?://)?(?:www\.)?youtube\.com/v/[\w-]+',
    ]
    return any(re.match(pattern, url) for pattern in youtube_patterns)

def extract_youtube_video_id(url):
    """Extract video ID from various YouTube URL formats"""
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def get_youtube_transcript_yt_dlp(url):
    """
    Get YouTube transcript using yt-dlp (more reliable than pytube)
    """
    try:
        ydl_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': ['en', 'en-US', 'en-GB'],
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Try to get manual subtitles first, then automatic
            subtitles = info.get('subtitles', {})
            auto_subtitles = info.get('automatic_captions', {})
            
            transcript_text = ""
            title = info.get('title', 'Unknown Title')
            
            # Try English subtitles first
            for lang in ['en', 'en-US', 'en-GB']:
                if lang in subtitles:
                    # Get the subtitle URL
                    subtitle_info = subtitles[lang]
                    for sub in subtitle_info:
                        if sub.get('ext') == 'vtt':
                            subtitle_url = sub.get('url')
                            if subtitle_url:
                                response = requests.get(subtitle_url)
                                transcript_text = clean_vtt_text(response.text)
                                break
                    if transcript_text:
                        break
                
                # If no manual subtitles, try automatic
                if not transcript_text and lang in auto_subtitles:
                    subtitle_info = auto_subtitles[lang]
                    for sub in subtitle_info:
                        if sub.get('ext') == 'vtt':
                            subtitle_url = sub.get('url')
                            if subtitle_url:
                                response = requests.get(subtitle_url)
                                transcript_text = clean_vtt_text(response.text)
                                break
                    if transcript_text:
                        break
            
            if transcript_text:
                return [Document(page_content=transcript_text, metadata={"title": title, "source": url})]
            else:
                return None
                
    except Exception as e:
        st.error(f"yt-dlp error: {str(e)}")
        return None

def clean_vtt_text(vtt_content):
    """Clean VTT subtitle content to extract just the text"""
    lines = vtt_content.split('\n')
    text_lines = []
    
    for line in lines:
        line = line.strip()
        # Skip VTT headers, timestamps, and empty lines
        if (line and 
            not line.startswith('WEBVTT') and 
            not line.startswith('NOTE') and
            not re.match(r'^\d+$', line) and  # Skip sequence numbers
            not re.match(r'^\d{2}:\d{2}:\d{2}', line) and  # Skip timestamps
            '-->' not in line):
            # Remove HTML tags and clean up
            clean_line = re.sub(r'<[^>]+>', '', line)
            clean_line = re.sub(r'&[a-zA-Z]+;', '', clean_line)  # Remove HTML entities
            if clean_line.strip():
                text_lines.append(clean_line.strip())
    
    return ' '.join(text_lines)

def get_youtube_transcript_fallback(url):
    """
    Fallback method using direct YouTube transcript API
    """
    try:
        video_id = extract_youtube_video_id(url)
        if not video_id:
            return None
            
        # Try to get transcript
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
        
        # Try to find English transcript
        try:
            transcript = transcript_list.find_transcript(['en', 'en-US', 'en-GB'])
            transcript_data = transcript.fetch()
            
            # Combine all text
            full_text = ' '.join([item['text'] for item in transcript_data])
            
            return [Document(page_content=full_text, metadata={"source": url})]
            
        except:
            # Try any available transcript
            for transcript in transcript_list:
                try:
                    transcript_data = transcript.fetch()
                    full_text = ' '.join([item['text'] for item in transcript_data])
                    return [Document(page_content=full_text, metadata={"source": url})]
                except:
                    continue
                    
        return None
        
    except Exception as e:
        st.error(f"YouTube Transcript API error: {str(e)}")
        return None

if groq_api_key.strip():
    llm = ChatGroq(model="llama-3.1-8b-instant", groq_api_key=groq_api_key)
    if st.button("Summarize Content"):
        ## Validate all the inputs
        if not generic_url.strip() or not validators.url(generic_url):
            st.error("Please enter a valid URL. Either YouTube video or website URL")
        else:
            try:
                with st.spinner("Loading content..."):
                    docs = None
                    
                    if is_youtube_url(generic_url):
                        st.info("Detected YouTube URL. Attempting to extract transcript...")
                        
                        # Try yt-dlp first (most reliable)
                        docs = get_youtube_transcript_yt_dlp(generic_url)
                        
                        # Fallback to youtube-transcript-api
                        if not docs:
                            st.info("Trying alternative transcript method...")
                            docs = get_youtube_transcript_fallback(generic_url)
                        
                        # If still no transcript, try loading as regular webpage
                        if not docs:
                            st.warning("No transcript available. Trying to load as webpage...")
                            try:
                                loader = UnstructuredURLLoader(
                                    urls=[generic_url], 
                                    ssl_verify=False, 
                                    headers=headers
                                )
                                docs = loader.load()
                            except Exception as web_exc:
                                st.error(f"Failed to load as webpage: {web_exc}")
                    
                    else:
                        # Handle regular websites
                        st.info("Loading website content...")
                        try:
                            loader = UnstructuredURLLoader(
                                urls=[generic_url], 
                                ssl_verify=False, 
                                headers=headers
                            )
                            docs = loader.load()
                        except Exception as web_exc:
                            st.error(f"Failed to load website: {web_exc}")
                            st.text(traceback.format_exc())

                    # Process the documents if we have them
                    if docs:
                        # Check if docs contains valid content
                        if isinstance(docs, list) and len(docs) > 0:
                            content = ""
                            for doc in docs:
                                if hasattr(doc, 'page_content') and doc.page_content.strip():
                                    content += doc.page_content.strip() + " "
                            
                            if content.strip():
                                with st.spinner("Generating summary..."):
                                    chain = load_summarize_chain(llm, chain_type="stuff", prompt=prompt)
                                    output_summary = chain.run(docs)
                                    st.success(output_summary)
                            else:
                                st.error("No readable content found at the provided URL.")
                        else:
                            st.error("No content could be extracted from the URL.")
                    else:
                        st.error("Failed to load content from the provided URL.")
                        
            except Exception as e:
                st.error(f"An unexpected error occurred: {e}")
                st.text(traceback.format_exc())
else:
    st.info("Please enter your Groq API Key to use the app")

# Troubleshooting hints
with st.sidebar:
    st.subheader("Troubleshooting")
    st.markdown("""
    - If YouTube videos fail, the transcript may not be available
    - Some videos have disabled captions
    - Private videos cannot be accessed
    - Try the URL in a browser first to verify it works
    """)