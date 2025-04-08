import os
import logging
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import groq
import re
from langdetect import detect, LangDetectException

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Replace with your actual tokens/keys
TELEGRAM_TOKEN = ""
GROQ_API_KEY = ""

# Initialize Groq client
groq_client = groq.Client(api_key=GROQ_API_KEY)

# Limited language support - only English and Hindi
LANGUAGE_CODES = {
    'en': 'English',
    'hi': 'Hindi',
    'hi-en': 'Hinglish'  # Custom code for Hinglish
}

# Function to validate YouTube URL
def is_valid_youtube_url(url):
    youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})'
    match = re.match(youtube_regex, url)
    return match is not None

# Function to extract video info using yt-dlp with language support
async def get_video_transcript(url, language_code='en'):
    try:
        # For Hinglish, we'll use English transcripts
        transcript_lang = 'en' if language_code == 'hi-en' else language_code
        
        ydl_opts = {
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [transcript_lang],
            'skip_download': True,
            'quiet': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Try to get subtitles in the specified language
            if 'subtitles' in info and transcript_lang in info['subtitles']:
                subtitle_url = info['subtitles'][transcript_lang][0]['url']
                # Here you would download and parse the subtitle file
                return f"Transcript extraction successful in {LANGUAGE_CODES.get(language_code, language_code)}", info['title'], language_code
            
            # If regular subtitles not available, try auto-generated ones
            elif 'automatic_captions' in info and transcript_lang in info['automatic_captions']:
                subtitle_url = info['automatic_captions'][transcript_lang][0]['url']
                # Here you would download and parse the subtitle file
                return f"Transcript extraction successful in {LANGUAGE_CODES.get(language_code, language_code)}", info['title'], language_code
            
            # If specified language not available, try English as fallback
            elif transcript_lang != 'en':
                logger.info(f"No transcript in {transcript_lang} available, trying English")
                return await get_video_transcript(url, 'en')
            
            else:
                # Try to get any available language
                available_langs = list(info.get('subtitles', {}).keys()) + list(info.get('automatic_captions', {}).keys())
                if available_langs:
                    first_lang = available_langs[0]
                    logger.info(f"Using available language: {first_lang}")
                    return await get_video_transcript(url, first_lang)
                else:
                    return "No transcript available for this video in any language", info['title'], None
    
    except Exception as e:
        logger.error(f"Error extracting video info: {e}")
        return f"Error processing video: {str(e)}", "Unknown Video", None

# Function to download YouTube video
# Modify the download function to catch and handle the FFmpeg error
async def download_youtube_video(update: Update, context: CallbackContext, url, format_id):
    user_lang = context.user_data.get('language', 'en')
    
    status_message = await update.callback_query.message.reply_text(
        get_localized_text('downloading_video', user_lang)
    )
    
    chat_id = update.effective_chat.id
    download_folder = f"downloads/{chat_id}"
    os.makedirs(download_folder, exist_ok=True)
    
    try:
        if format_id == 'audio_only':
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'writethumbnail': True,
            }
        elif format_id == 'video_audio_720p':
            ydl_opts = {
                'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
                'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
                'merge_output_format': 'mp4',
            }
        elif format_id == 'video_audio_360p':
            ydl_opts = {
                'format': 'bestvideo[height<=360]+bestaudio/best[height<=360]',
                'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
                'merge_output_format': 'mp4',
            }
        else:  # video_audio_best
            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
                'merge_output_format': 'mp4',
            }
        
        # For non-audio formats, check if we should add a fallback option
        if format_id != 'audio_only':
            ydl_opts['ignoreerrors'] = True
            ydl_opts['nooverwrites'] = True
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Handle postprocessed files (like mp3)
                if format_id == 'audio_only':
                    base_filename = os.path.splitext(filename)[0]
                    filename = f"{base_filename}.mp3"
                
                # Check if the file exists
                if os.path.exists(filename):
                    file_size = os.path.getsize(filename) / (1024 * 1024)  # Size in MB
                    
                    if file_size <= 50:
                        await status_message.edit_text(get_localized_text('uploading_to_telegram', user_lang))
                        
                        if format_id == 'audio_only':
                            await context.bot.send_audio(
                                chat_id=chat_id,
                                audio=open(filename, 'rb'),
                                title=info.get('title', 'YouTube Audio'),
                                caption=f"{info.get('title', 'Downloaded Audio')}"
                            )
                        else:
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=open(filename, 'rb'),
                                caption=f"{info.get('title', 'Downloaded Video')}"
                            )
                        
                        await status_message.delete()
                    else:
                        await status_message.edit_text(
                            get_localized_text('file_too_large', user_lang).format(size=round(file_size, 2))
                        )
                else:
                    # Try a fallback format if the file doesn't exist (possible FFmpeg error)
                    raise Exception("File not created - FFmpeg may be missing")
                    
        except Exception as inner_e:
            if "ffmpeg is not installed" in str(inner_e) or "File not created" in str(inner_e):
                # Try again with a simpler format that doesn't require FFmpeg
                logger.info("FFmpeg error detected, trying fallback format")
                
                # Update status message
                await status_message.edit_text(
                    "FFmpeg not found. Trying alternative download method..."
                )
                
                # Simpler format that doesn't require merging
                fallback_opts = {
                    'format': 'best',  # This selects the best quality combined format
                    'outtmpl': f'{download_folder}/%(title)s.%(ext)s',
                }
                
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filename = ydl.prepare_filename(info)
                    
                    if os.path.exists(filename):
                        file_size = os.path.getsize(filename) / (1024 * 1024)
                        
                        if file_size <= 50:
                            await status_message.edit_text(get_localized_text('uploading_to_telegram', user_lang))
                            
                            await context.bot.send_video(
                                chat_id=chat_id,
                                video=open(filename, 'rb'),
                                caption=f"{info.get('title', 'Downloaded Video')} (Alternative Format)"
                            )
                            
                            await status_message.delete()
                        else:
                            await status_message.edit_text(
                                get_localized_text('file_too_large', user_lang).format(size=round(file_size, 2))
                            )
                    else:
                        await status_message.edit_text(
                            "Failed to download video. Please install FFmpeg for better video downloads."
                        )
            else:
                # Other error occurred
                raise inner_e
                
        # Clean up downloaded file
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception as e:
            logger.error(f"Error removing file {filename}: {e}")
            
    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        await status_message.edit_text(
            f"Error: {str(e)}. For video downloads, please install FFmpeg."
        ) 

# Map detected language to our supported languages
def map_to_supported_language(detected_lang):
    if detected_lang == 'hi':
        return 'hi'
    # Check for Hinglish - this is simplified and may need improvement
    # Hinglish detection is challenging as it's a mixed language
    elif detected_lang == 'en' and False:  # Add your Hinglish detection logic here
        return 'hi-en'
    else:
        return 'en'  # Default to English for all other languages

# Detect language of user message and map to supported languages
def detect_language(text):
    try:
        detected = detect(text)
        return map_to_supported_language(detected)
    except LangDetectException:
        return 'en'  # Default to English if detection fails

# Process the transcript with Groq based on user's choice and language
async def process_with_groq(transcript, title, choice, language_code='en'):
    # Prepare system message based on language
    if language_code == 'hi':
        system_message = "आप एक सहायक हैं जो YouTube वीडियो ट्रांसक्रिप्ट को प्रोसेस करता है। हिंदी में जवाब दें।"
    elif language_code == 'hi-en':
        system_message = "You are an assistant that processes YouTube video transcripts. Respond in Hinglish - a mix of Hindi and English as commonly spoken in India."
    else:
        system_message = "You are an assistant that processes YouTube video transcripts. Respond in English."
    
    # Prepare prompts based on language
    if language_code == 'hi':
        prompts = {
            "summary": f"निम्नलिखित YouTube वीडियो ट्रांसक्रिप्ट का एक संक्षिप्त सारांश प्रदान करें, शीर्षक '{title}':\n\n{transcript}",
            "key_points": f"इस YouTube वीडियो ट्रांसक्रिप्ट से महत्वपूर्ण बिंदुओं को निकालें और सूचीबद्ध करें, शीर्षक '{title}':\n\n{transcript}",
            "detailed_analysis": f"इस YouTube वीडियो ट्रांसक्रिप्ट '{title}' में मौजूद सामग्री का विस्तृत विश्लेषण प्रदान करें:\n\n{transcript}",
            "questions": f"इस YouTube वीडियो ट्रांसक्रिप्ट '{title}' की सामग्री के आधार पर 5 महत्वपूर्ण प्रश्न और उत्तर तैयार करें:\n\n{transcript}",
            "study_notes": f"इस YouTube वीडियो ट्रांसक्रिप्ट '{title}' के आधार पर एक संगठित प्रारूप में अध्ययन नोट्स बनाएं:\n\n{transcript}",
        }
    elif language_code == 'hi-en':
        prompts = {
            "summary": f"YouTube video transcript '{title}' ka ek concise summary provide karein:\n\n{transcript}",
            "key_points": f"Is YouTube video transcript '{title}' se key points extract karke list karein:\n\n{transcript}",
            "detailed_analysis": f"Is YouTube video transcript '{title}' ki content ka detailed analysis provide karein:\n\n{transcript}",
            "questions": f"Is YouTube video transcript '{title}' ki content ke based par 5 important questions aur answers generate karein:\n\n{transcript}",
            "study_notes": f"Is YouTube video transcript '{title}' ke based par organized format mein study notes create karein:\n\n{transcript}",
        }
    else:  # English
        prompts = {
            "summary": f"Provide a concise summary of the following YouTube video transcript titled '{title}':\n\n{transcript}",
            "key_points": f"Extract and list the key points from this YouTube video transcript titled '{title}':\n\n{transcript}",
            "detailed_analysis": f"Provide a detailed analysis of the content in this YouTube video transcript titled '{title}':\n\n{transcript}",
            "questions": f"Generate 5 important questions and answers based on the content of this YouTube video transcript titled '{title}':\n\n{transcript}",
            "study_notes": f"Create study notes in an organized format based on this YouTube video transcript titled '{title}':\n\n{transcript}",
        }
    
    try:
        # Call Groq API with appropriate prompt
        response = groq_client.chat.completions.create(
            model="llama3-70b-8192",  # Choose the appropriate model
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompts[choice]}
            ],
            temperature=0.7,
            max_tokens=1024
        )
        
        # Extract the response text
        return response.choices[0].message.content
        
    except Exception as e:
        logger.error(f"Error with Groq API: {e}")
        # Return error message in appropriate language
        if language_code == 'hi':
            return f"Groq API के साथ प्रोसेसिंग में त्रुटि: {str(e)}"
        elif language_code == 'hi-en':
            return f"Groq API ke saath processing mein error: {str(e)}"
        else:
            return f"Error processing with Groq API: {str(e)}"

# Helper function to get text in the appropriate language
def get_localized_text(text_key, language_code='en'):
    # Text localization dictionary
    localized_text = {
        'welcome_message': {
            'en': "Hello! I'm a YouTube Video Processing Bot. Send me a YouTube video link, and I'll help you analyze it using AI or download it. Just paste the URL and I'll guide you through the options.",
            'hi': "नमस्ते! मैं एक YouTube वीडियो प्रोसेसिंग बॉट हूं। मुझे एक YouTube वीडियो लिंक भेजें, और मैं AI का उपयोग करके इसका विश्लेषण करने या इसे डाउनलोड करने में आपकी मदद करूंगा। बस URL पेस्ट करें और मैं आपको विकल्पों के माध्यम से मार्गदर्शन करूंगा।",
            'hi-en': "Hello! Main ek YouTube Video Processing Bot hoon. Mujhe ek YouTube video link bhejein, aur main AI ka use karke uska analysis karne ya use download karne mein aapki help karunga. URL paste karein aur main aapko options ke through guide karunga."
        },
        'help_message': {
            'en': "How to use this bot:\n\n1. Send a YouTube video URL\n2. Select what you want to do with the video\n3. Wait for the AI to process your request or for the video to download\n\nAvailable commands:\n/start - Start the bot\n/help - Show this help message\n/language - Change your preferred language",
            'hi': "इस बॉट का उपयोग कैसे करें:\n\n1. एक YouTube वीडियो URL भेजें\n2. चुनें कि आप वीडियो के साथ क्या करना चाहते हैं\n3. AI द्वारा आपके अनुरोध को प्रोसेस करने या वीडियो डाउनलोड होने का इंतज़ार करें\n\nउपलब्ध कमांड:\n/start - बॉट शुरू करें\n/help - यह सहायता संदेश दिखाएं\n/language - अपनी पसंदीदा भाषा बदलें",
            'hi-en': "Is bot ko kaise use karein:\n\n1. Ek YouTube video URL bhejein\n2. Select karein ki aap video ke saath kya karna chahte hain\n3. AI ke dwara aapke request ko process karne ya video download hone ka wait karein\n\nAvailable commands:\n/start - Bot start karein\n/help - Yeh help message dikhayein\n/language - Apni preferred language change karein"
        },
        'invalid_url': {
            'en': "Please provide a valid YouTube URL.",
            'hi': "कृपया एक वैध YouTube URL प्रदान करें।",
            'hi-en': "Kripya ek valid YouTube URL provide karein."
        },
        'processing_url': {
            'en': "Processing your YouTube video link...",
            'hi': "आपके YouTube वीडियो लिंक को प्रोसेस किया जा रहा है...",
            'hi-en': "Aapke YouTube video link ko process kiya ja raha hai..."
        },
        'what_to_do': {
            'en': "What would you like to do with this video?",
            'hi': "आप इस वीडियो के साथ क्या करना चाहेंगे?",
            'hi-en': "Aap is video ke saath kya karna chahenge?"
        },
        'no_transcript': {
            'en': "Sorry, I couldn't retrieve the transcript for this video. Please try another video or check if the video has captions available.",
            'hi': "क्षमा करें, मैं इस वीडियो के लिए ट्रांसक्रिप्ट प्राप्त नहीं कर सका। कृपया किसी अन्य वीडियो का प्रयास करें या जांचें कि क्या वीडियो में कैप्शन उपलब्ध हैं।",
            'hi-en': "Sorry, main is video ke liye transcript retrieve nahi kar saka. Please kisi aur video ko try karein ya check karein ki kya video mein captions available hain."
        },
        'processing_request': {
            'en': "Processing your request for {choice}. This may take a moment...",
            'hi': "आपके {choice} के अनुरोध को प्रोसेस किया जा रहा है। इसमें कुछ समय लग सकता है...",
            'hi-en': "Aapke {choice} ke request ko process kiya ja raha hai. Isme kuch samay lag sakta hai..."
        },
        'result_intro': {
            'en': "Here's your {choice} for the video:",
            'hi': "वीडियो के लिए आपका {choice} यहां है:",
            'hi-en': "Video ke liye aapka {choice} yahan hai:"
        },
        'language_set': {
            'en': "Language set to English.",
            'hi': "भाषा हिंदी पर सेट की गई।",
            'hi-en': "Language Hinglish par set ki gayi."
        },
        'select_language': {
            'en': "Please select your preferred language:",
            'hi': "कृपया अपनी पसंदीदा भाषा चुनें:",
            'hi-en': "Kripya apni pasandida language select karein:"
        },
        'download_options': {
            'en': "Select download format:",
            'hi': "डाउनलोड फॉर्मेट चुनें:",
            'hi-en': "Download format select karein:"
        },
        'downloading_video': {
            'en': "Downloading your video. This might take a while depending on the video length and quality...",
            'hi': "आपका वीडियो डाउनलोड किया जा रहा है। वीडियो की लंबाई और गुणवत्ता के आधार पर इसमें कुछ समय लग सकता है...",
            'hi-en': "Aapka video download kiya ja raha hai. Video ki length aur quality ke hisaab se isme kuch time lag sakta hai..."
        },
        'uploading_to_telegram': {
            'en': "Download complete! Uploading to Telegram...",
            'hi': "डाउनलोड पूरा हुआ! टेलीग्राम पर अपलोड हो रहा है...",
            'hi-en': "Download complete! Telegram par upload ho raha hai..."
        },
        'file_too_large': {
            'en': "The file is too large to send via Telegram ({size}MB). Telegram has a 50MB file size limit. Please try a different format or a shorter video.",
            'hi': "फ़ाइल टेलीग्राम के माध्यम से भेजने के लिए बहुत बड़ी है ({size}MB)। टेलीग्राम में 50MB फ़ाइल साइज की सीमा है। कृपया एक अलग फॉर्मेट या छोटे वीडियो का प्रयास करें।",
            'hi-en': "File Telegram ke through send karne ke liye bahut badi hai ({size}MB). Telegram mein 50MB file size ki limit hai. Please ek different format ya chote video ka try karein."
        },
        'download_failed': {
            'en': "Download failed. The file wasn't found after processing.",
            'hi': "डाउनलोड विफल। प्रोसेसिंग के बाद फ़ाइल नहीं मिली।",
            'hi-en': "Download fail ho gaya. Processing ke baad file nahi mili."
        },
        'download_error': {
            'en': "An error occurred during download: {error}",
            'hi': "डाउनलोड के दौरान एक त्रुटि हुई: {error}",
            'hi-en': "Download ke dauran ek error hua: {error}"
        },
        'select_option': {
            'en': "Please select an option:",
            'hi': "कृपया एक विकल्प चुनें:",
            'hi-en': "Kripya ek option select karein:"
        }
    }
    
    # Get the text in the requested language, fallback to English if not available
    return localized_text.get(text_key, {}).get(language_code, localized_text.get(text_key, {}).get('en', 'Text not found'))

# Command handlers
async def start(update: Update, context: CallbackContext) -> None:
    user_lang = detect_language(update.message.text)
    context.user_data['language'] = user_lang
    
    await update.message.reply_text(get_localized_text('welcome_message', user_lang))

async def help_command(update: Update, context: CallbackContext) -> None:
    user_lang = context.user_data.get('language', detect_language(update.message.text))
    
    await update.message.reply_text(get_localized_text('help_message', user_lang))

async def set_language(update: Update, context: CallbackContext) -> None:
    user_lang = context.user_data.get('language', 'en')
    
    # Create keyboard with language options (only English, Hindi, and Hinglish)
    keyboard = [
        [InlineKeyboardButton("English", callback_data="lang_en")],
        [InlineKeyboardButton("हिंदी (Hindi)", callback_data="lang_hi")],
        [InlineKeyboardButton("हिंग्लिश (Hinglish)", callback_data="lang_hi-en")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        get_localized_text('select_language', user_lang),
        reply_markup=reply_markup
    )

# Handle YouTube links
async def handle_youtube_url(update: Update, context: CallbackContext) -> None:
    url = update.message.text
    user_lang = context.user_data.get('language', detect_language(update.message.text))
    
    if not is_valid_youtube_url(url):
        await update.message.reply_text(get_localized_text('invalid_url', user_lang))
        return
    
    # Store the URL in user data for later use
    context.user_data['youtube_url'] = url
    
    await update.message.reply_text(get_localized_text('processing_url', user_lang))
    
    # Extract transcript in user's language
    transcript, title, detected_lang = await get_video_transcript(url, user_lang)
    
    # Store transcript and title in user data
    context.user_data['transcript'] = transcript
    context.user_data['title'] = title
    
    # Create keyboard with options
    if user_lang == 'en':
        keyboard = [
            [InlineKeyboardButton("Generate Summary", callback_data='summary')],
            [InlineKeyboardButton("Extract Key Points", callback_data='key_points')],
            [InlineKeyboardButton("Detailed Analysis", callback_data='detailed_analysis')],
            [InlineKeyboardButton("Generate Q&A", callback_data='questions')],
            [InlineKeyboardButton("Create Study Notes", callback_data='study_notes')],
            [InlineKeyboardButton("Download Video", callback_data='download_video')]
        ]
    elif user_lang == 'hi':
        keyboard = [
            [InlineKeyboardButton("सारांश जनरेट करें", callback_data='summary')],
            [InlineKeyboardButton("महत्वपूर्ण बिंदु निकालें", callback_data='key_points')],
            [InlineKeyboardButton("विस्तृत विश्लेषण", callback_data='detailed_analysis')],
            [InlineKeyboardButton("प्रश्न-उत्तर जनरेट करें", callback_data='questions')],
            [InlineKeyboardButton("अध्ययन नोट्स बनाएं", callback_data='study_notes')],
            [InlineKeyboardButton("वीडियो डाउनलोड करें", callback_data='download_video')]
        ]
    else:  # Hinglish
        keyboard = [
            [InlineKeyboardButton("Summary Generate Karein", callback_data='summary')],
            [InlineKeyboardButton("Key Points Nikaalein", callback_data='key_points')],
            [InlineKeyboardButton("Detailed Analysis", callback_data='detailed_analysis')],
            [InlineKeyboardButton("Q&A Generate Karein", callback_data='questions')],
            [InlineKeyboardButton("Study Notes Banayein", callback_data='study_notes')],
            [InlineKeyboardButton("Video Download Karein", callback_data='download_video')]
        ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Video: {title}\n\n{get_localized_text('what_to_do', user_lang)}",
        reply_markup=reply_markup
    )

# Handle button callbacks
async def button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    
    callback_data = query.data
    user_lang = context.user_data.get('language', 'en')
    
    # Handle language selection callback
    if callback_data.startswith('lang_'):
        selected_lang = callback_data.split('_')[1]
        context.user_data['language'] = selected_lang
        
        await query.message.reply_text(get_localized_text('language_set', selected_lang))
        return
    
    # Handle download video option
    if callback_data == 'download_video':
        # Show download format options
        if user_lang == 'en':
            keyboard = [
                [InlineKeyboardButton("Audio Only (MP3)", callback_data='download_audio_only')],
                [InlineKeyboardButton("Video 360p", callback_data='download_video_audio_360p')],
                [InlineKeyboardButton("Video 720p", callback_data='download_video_audio_720p')],
                [InlineKeyboardButton("Best Quality", callback_data='download_video_audio_best')]
            ]
        elif user_lang == 'hi':
            keyboard = [
                [InlineKeyboardButton("केवल ऑडियो (MP3)", callback_data='download_audio_only')],
                [InlineKeyboardButton("वीडियो 360p", callback_data='download_video_audio_360p')],
                [InlineKeyboardButton("वीडियो 720p", callback_data='download_video_audio_720p')],
                [InlineKeyboardButton("बेस्ट क्वालिटी", callback_data='download_video_audio_best')]
            ]
        else:  # Hinglish
            keyboard = [
                [InlineKeyboardButton("Sirf Audio (MP3)", callback_data='download_audio_only')],
                [InlineKeyboardButton("Video 360p", callback_data='download_video_audio_360p')],
                [InlineKeyboardButton("Video 720p", callback_data='download_video_audio_720p')],
                [InlineKeyboardButton("Best Quality", callback_data='download_video_audio_best')]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            get_localized_text('download_options', user_lang),
            reply_markup=reply_markup
        )
        return
    
    # Handle download format selection
    if callback_data.startswith('download_'):
        url = context.user_data.get('youtube_url')
        format_id = callback_data.replace('download_', '')
        
        if url:
            await download_youtube_video(update, context, url, format_id)
        return
    
    # Handle video processing options
    choice = callback_data
    transcript = context.user_data.get('transcript')
    title = context.user_data.get('title')
    
    if not transcript or transcript.startswith("No transcript") or transcript.startswith("Error"):
        await query.message.reply_text(get_localized_text('no_transcript', user_lang))
        return
    
    choice_text = {
        'summary': 'summary',
        'key_points': 'key points',
        'detailed_analysis': 'detailed analysis',
        'questions': 'Q&A',
        'study_notes': 'study notes'
    }
    
    # Let the user know we're processing their request
    processing_message = await query.message.reply_text(
        get_localized_text('processing_request', user_lang).format(choice=choice_text.get(choice, choice))
    )
    
    # Process with Groq AI
    result = await process_with_groq(transcript, title, choice, user_lang)
    
    # Send the result, potentially split into multiple messages if too long
    max_message_length = 4096  # Telegram message limit
    
    if len(result) <= max_message_length:
        await processing_message.edit_text(
            f"{get_localized_text('result_intro', user_lang).format(choice=choice_text.get(choice, choice))}\n\n{result}"
        )
    else:
        # Split into multiple messages
        await processing_message.edit_text(
            get_localized_text('result_intro', user_lang).format(choice=choice_text.get(choice, choice))
        )
        
        # Send the content in chunks
        for i in range(0, len(result), max_message_length):
            chunk = result[i:i + max_message_length]
            await query.message.reply_text(chunk)

# Create application and add handlers
def main() -> None:
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("language", set_language))
    
    # Handle YouTube URLs
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.Regex(r'https?://'),
        handle_youtube_url
    ))
    
    # Handle callback queries
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()
