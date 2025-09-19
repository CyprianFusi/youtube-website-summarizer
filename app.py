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
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    YOUTUBE_TRANSCRIPT_AVAILABLE = True
except ImportError:
    YOUTUBE_TRANSCRIPT_AVAILABLE = False
    st.warning("YouTube Transcript API not available. Only yt-dlp method will be used for YouTube videos.")

from bs4 import BeautifulSoup

## Streamlit APP
st.set_page_config(page_title="LangChain: Summarize Text From YouTube or Website", page_icon="🦜")
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
                                try:
                                    response = requests.get(subtitle_url, timeout=10)
                                    response.raise_for_status()
                                    transcript_text = clean_vtt_text(response.text)
                                    break
                                except requests.RequestException:
                                    continue
                    if transcript_text:
                        break
                
                # If no manual subtitles, try automatic
                if not transcript_text and lang in auto_subtitles:
                    subtitle_info = auto_subtitles[lang]
                    for sub in subtitle_info:
                        if sub.get('ext') == 'vtt':
                            subtitle_url = sub.get('url')
                            if subtitle_url:
                                try:
                                    response = requests.get(subtitle_url, timeout=10)
                                    response.raise_for_status()
                                    transcript_text = clean_vtt_text(response.text)
                                    break
                                except requests.RequestException:
                                    continue
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
        
        # Try different approaches for getting transcript
        try:
            # Method 1: Try to get transcript directly
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'en-US', 'en-GB'])
            full_text = ' '.join([item['text'] for item in transcript_data])
            return [Document(page_content=full_text, metadata={"source": url})]
            
        except Exception as direct_error:
            try:
                # Method 2: Try with any available language
                transcript_data = YouTubeTranscriptApi.get_transcript(video_id)
                full_text = ' '.join([item['text'] for item in transcript_data])
                return [Document(page_content=full_text, metadata={"source": url})]
                
            except Exception as any_lang_error:
                try:
                    # Method 3: List available transcripts and try each
                    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                    
                    # Try to find English transcript first
                    for transcript in transcript_list:
                        try:
                            if transcript.language_code in ['en', 'en-US', 'en-GB']:
                                transcript_data = transcript.fetch()
                                full_text = ' '.join([item['text'] for item in transcript_data])
                                return [Document(page_content=full_text, metadata={"source": url})]
                        except:
                            continue
                    
                    # If no English, try any available transcript
                    for transcript in transcript_list:
                        try:
                            transcript_data = transcript.fetch()
                            full_text = ' '.join([item['text'] for item in transcript_data])
                            return [Document(page_content=full_text, metadata={"source": url})]
                        except:
                            continue
                            
                except Exception as list_error:
                    st.error(f"YouTube Transcript API error: {str(list_error)}")
                    return None
                    
        return None
        
    except Exception as e:
        st.error(f"YouTube Transcript API error: {str(e)}")
        return None

def get_webpage_content_beautiful_soup(url):
    """
    Alternative webpage content extraction using BeautifulSoup
    """
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "aside", "header"]):
            script.decompose()
        
        # Get main content - try common content containers first
        main_content = (
            soup.find('main') or 
            soup.find('article') or 
            soup.find('div', class_=re.compile(r'content|main|article', re.I)) or
            soup.find('div', id=re.compile(r'content|main|article', re.I)) or
            soup.body or
            soup
        )
        
        # Extract text
        text = main_content.get_text(separator=' ', strip=True)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        
        if len(text) < 100:  # Too short, probably didn't get good content
            return None
            
        # Get title
        title_tag = soup.find('title')
        title = title_tag.get_text().strip() if title_tag else "Unknown Title"
        
        return [Document(page_content=text, metadata={"title": title, "source": url})]
        
    except Exception as e:
        st.error(f"BeautifulSoup webpage extraction error: {str(e)}")
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
                        st.info("🎥 Detected YouTube URL. Attempting to extract transcript...")
                        
                        # Try yt-dlp first (most reliable)
                        with st.spinner("Trying primary transcript extraction method..."):
                            docs = get_youtube_transcript_yt_dlp(generic_url)
                        
                        # Fallback to youtube-transcript-api
                        if not docs and YOUTUBE_TRANSCRIPT_AVAILABLE:
                            st.info("🔄 Trying alternative transcript method...")
                            with st.spinner("Using fallback transcript method..."):
                                docs = get_youtube_transcript_fallback(generic_url)
                        
                        # If still no transcript, try loading as regular webpage
                        if not docs:
                            st.warning("⚠️ No transcript available. Trying to extract video description...")
                            try:
                                docs = get_webpage_content_beautiful_soup(generic_url)
                                if not docs:
                                    # Final fallback to UnstructuredURLLoader
                                    loader = UnstructuredURLLoader(
                                        urls=[generic_url], 
                                        ssl_verify=False, 
                                        headers=headers
                                    )
                                    docs = loader.load()
                            except Exception as web_exc:
                                st.error(f"Failed to load YouTube page content: {web_exc}")
                    
                    else:
                        # Handle regular websites
                        st.info("🌐 Loading website content...")
                        
                        # Try BeautifulSoup first for better content extraction
                        docs = get_webpage_content_beautiful_soup(generic_url)
                        
                        if not docs:
                            st.info("🔄 Trying alternative website extraction method...")
                            try:
                                loader = UnstructuredURLLoader(
                                    urls=[generic_url], 
                                    ssl_verify=False, 
                                    headers=headers
                                )
                                docs = loader.load()
                            except Exception as web_exc:
                                st.error(f"Failed to load website: {web_exc}")

                    # Process the documents if we have them
                    if docs:
                        # Check if docs contains valid content
                        if isinstance(docs, list) and len(docs) > 0:
                            content = ""
                            total_chars = 0
                            
                            for doc in docs:
                                if hasattr(doc, 'page_content') and doc.page_content.strip():
                                    content += doc.page_content.strip() + " "
                                    total_chars += len(doc.page_content.strip())
                            
                            if content.strip() and total_chars > 50:  # Minimum content threshold
                                st.success(f"✅ Successfully extracted {total_chars:,} characters of content")
                                
                                # Show preview of content
                                with st.expander("Preview extracted content"):
                                    st.text(content[:500] + "..." if len(content) > 500 else content)
                                
                                with st.spinner("🤖 Generating summary..."):
                                    chain = load_summarize_chain(llm, chain_type="stuff", prompt=prompt)
                                    output_summary = chain.run(docs)
                                    
                                    st.subheader("📋 Summary")
                                    st.success(output_summary)
                                    
                                    # Add word count
                                    word_count = len(output_summary.split())
                                    st.caption(f"Summary length: {word_count} words")
                            else:
                                st.error("❌ No readable content found at the provided URL.")
                                st.info("This might be due to:")
                                st.markdown("""
                                - The page requires JavaScript to load content
                                - The content is behind a login wall
                                - The page structure is not accessible
                                - Anti-bot protection is active
                                """)
                        else:
                            st.error("❌ No content could be extracted from the URL.")
                    else:
                        st.error("❌ Failed to load content from the provided URL.")
                        
            except Exception as e:
                st.error(f"❌ An unexpected error occurred: {e}")
                with st.expander("View detailed error"):
                    st.text(traceback.format_exc())
else:
    st.info("🔑 Please enter your Groq API Key to use the app")

# Enhanced troubleshooting hints
with st.sidebar:
    st.subheader("Troubleshooting")
    st.markdown("""
    **YouTube Videos:**
    - If YouTube videos fail, the transcript may not be available
    - Some videos have disabled captions
    - Private videos cannot be accessed
    - Age-restricted content may not work
    
    **Websites:**
    - Some sites block automated access
    - JavaScript-heavy sites may not load properly
    - Login-required content cannot be accessed
    
    **General:**
    - Try the URL in a browser first to verify it works
    - Make sure the URL is publicly accessible
    """)
    
    st.subheader("Supported URL Types")
    st.markdown("""
    ✅ **YouTube:** youtube.com, youtu.be  
    ✅ **News sites:** Most major news websites  
    ✅ **Blogs:** Medium, WordPress sites  
    ✅ **Documentation:** GitHub, docs sites  
    ❌ **Social media:** Twitter, Facebook (limited)  
    ❌ **Paywalled content:** NY Times, WSJ (subscriber content)
    """)