import os
import smtplib
import subprocess
import time
import random
import json
import re
import hashlib
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlparse, urljoin
from pathlib import Path
import io

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify
from PIL import Image

from config import (
    SUBREDDITS,
    POST_LIMIT_PER_SUBREDDIT,
    TOP_COMMENTS_PER_POST,
    MIN_SCORE,
    KINDLE,
    COMMENT_DEPTH,
    INCLUDE_NESTED_COMMENTS,
    get_session,
    CORS_PROXIES,
    get_proxy_url,
    USE_PROXY,
    INCLUDE_IMAGES,
    DOWNLOAD_IMAGES,
    KINDLE_IMAGE_WIDTH,
    KINDLE_IMAGE_QUALITY,
    KINDLE_MAX_IMAGE_SIZE_MB,
)

OUTPUT_DIR = "output"
IMAGES_DIR = os.path.join(OUTPUT_DIR, "images")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)

# Create a global session that persists across requests
session = get_session()
current_proxy_index = 0

# Track downloaded images to avoid duplicates
downloaded_images = {}


def switch_proxy():
    """Switch to next available proxy"""
    global current_proxy_index
    current_proxy_index += 1
    if current_proxy_index >= len(CORS_PROXIES):
        current_proxy_index = 0
    print(f"Switching to proxy {current_proxy_index + 1}/{len(CORS_PROXIES)}: {CORS_PROXIES[current_proxy_index]}")


def is_valid_url(url):
    """Check if URL is valid and has proper scheme"""
    if not url or not isinstance(url, str):
        return False
    try:
        result = urlparse(url)
        return result.scheme in ['http', 'https'] and bool(result.netloc)
    except:
        return False


def extract_image_urls(text):
    """Extract image URLs from text (markdown and HTML)"""
    urls = []
    
    if not text:
        return urls
    
    # Match markdown image syntax: ![alt](url)
    markdown_images = re.findall(r'!\[.*?\]\((.*?)\)', text)
    urls.extend(markdown_images)
    
    # Match direct image URLs with common extensions
    direct_images = re.findall(
        r'(https?://[^\s\)]+\.(?:jpg|jpeg|png|gif|webp|webpd|bmp|svg|ico|tiff|tif)(?:\?[^\s]*)?)', 
        text, 
        re.IGNORECASE
    )
    urls.extend(direct_images)
    
    # Match Reddit gallery URLs
    reddit_galleries = re.findall(r'(https?://(?:www\.)?reddit\.com/gallery/[^\s\)]+)', text, re.IGNORECASE)
    urls.extend(reddit_galleries)
    
    # Match Imgur links (convert to direct image URLs)
    imgur_links = re.findall(r'(https?://(?:i\.)?imgur\.com/([a-zA-Z0-9]+)(?:\.\w+)?)', text)
    for link, img_id in imgur_links:
        urls.append(f"https://i.imgur.com/{img_id}.jpg")
    
    # Match Reddit image URLs (various subdomains)
    reddit_images = re.findall(
        r'(https?://(?:i\.redd\.it|external-preview\.redd\.it|preview\.redd\.it|thumbs\.reddit\.com|b\.thumbs\.reddit\.com)/[^\s\)]+)', 
        text, 
        re.IGNORECASE
    )
    urls.extend(reddit_images)
    
    # Match Reddit media URLs
    reddit_media = re.findall(r'(https?://(?:www\.)?reddit\.com/media/[^\s\)]+)', text, re.IGNORECASE)
    urls.extend(reddit_media)
    
    # Clean and validate URLs
    valid_urls = []
    for url in urls:
        # Skip GIPHY (videos, not static images)
        if 'giphy.com' in url or 'giphy|' in url:
            continue
            
        # Skip video platforms
        if any(skip in url.lower() for skip in ['youtube.com', 'youtu.be', 'vimeo.com', 'tiktok.com']):
            continue
        
        # Fix malformed URLs
        if '|' in url and 'http' not in url:
            parts = url.split('|')
            if len(parts) > 1 and parts[0] in ['giphy', 'imgur', 'reddit']:
                continue
            url = f"https://{parts[0]}.com/{parts[1]}"
        
        if is_valid_url(url):
            valid_urls.append(url)
    
    return list(set(valid_urls))


def get_image_extension(content_type, url):
    """Determine proper file extension from content-type or URL"""
    content_type = content_type.lower()
    url_lower = url.lower()
    
    # Check content-type first
    if 'webp' in content_type:
        return 'webp'
    elif 'jpeg' in content_type or 'jpg' in content_type:
        return 'jpg'
    elif 'png' in content_type:
        return 'png'
    elif 'gif' in content_type:
        return 'gif'
    elif 'bmp' in content_type:
        return 'bmp'
    elif 'svg' in content_type:
        return 'svg'
    
    # Fallback to URL extension
    if url_lower.endswith('.webp') or 'webp' in url_lower:
        return 'webp'
    elif url_lower.endswith('.jpg') or url_lower.endswith('.jpeg'):
        return 'jpg'
    elif url_lower.endswith('.png'):
        return 'png'
    elif url_lower.endswith('.gif'):
        return 'gif'
    elif url_lower.endswith('.bmp'):
        return 'bmp'
    elif url_lower.endswith('.svg'):
        return 'svg'
    
    # Default to jpg
    return 'jpg'


def resize_image_for_kindle(image_path):
    """
    Resize and compress image to Kindle-friendly dimensions and size.
    Returns: path to resized image
    """
    try:
        # Open image
        with Image.open(image_path) as img:
            original_size = os.path.getsize(image_path) / (1024 * 1024)  # Size in MB
            original_format = img.format
            original_mode = img.mode
            
            # Store original dimensions
            original_width, original_height = img.size
            
            # Check if resize is needed
            needs_resize = False
            
            # Resize if width exceeds Kindle default width
            if original_width > KINDLE_IMAGE_WIDTH:
                needs_resize = True
                # Calculate new height maintaining aspect ratio
                ratio = KINDLE_IMAGE_WIDTH / original_width
                new_width = KINDLE_IMAGE_WIDTH
                new_height = int(original_height * ratio)
                
                # Resize image
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                print(f"      📏 Resized: {original_width}x{original_height} -> {new_width}x{new_height}")
            
            # Always compress to reduce file size
            needs_resize = True  # Force compression even if dimensions are okay
            
            # Convert RGBA to RGB for JPEG compatibility
            if img.mode in ('RGBA', 'LA', 'P'):
                # Create white background for transparency
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                if img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[-1])
                else:
                    background.paste(img)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Create temporary file for compressed image
            temp_path = image_path + '.temp.jpg'
            
            # Save with compression (always use JPEG for better compression)
            img.save(temp_path, 'JPEG', quality=KINDLE_IMAGE_QUALITY, optimize=True)
            
            # Check compressed size
            new_size = os.path.getsize(temp_path) / (1024 * 1024)
            
            # If still too large, reduce quality further
            if new_size > KINDLE_MAX_IMAGE_SIZE_MB:
                quality_adjusted = max(30, KINDLE_IMAGE_QUALITY - 20)
                img.save(temp_path, 'JPEG', quality=quality_adjusted, optimize=True)
                new_size = os.path.getsize(temp_path) / (1024 * 1024)
                print(f"      🔧 Further compressed to {quality_adjusted}% quality")
            
            # Replace original file
            os.remove(image_path)
            os.rename(temp_path, image_path)
            
            # Update extension to .jpg
            new_filename = image_path.rsplit('.', 1)[0] + '.jpg'
            if new_filename != image_path:
                os.rename(image_path, new_filename)
                image_path = new_filename
            
            print(f"      💾 Compressed: {original_size:.2f}MB -> {new_size:.2f}MB ({KINDLE_IMAGE_QUALITY}% quality)")
            return image_path
            
    except Exception as e:
        print(f"      ⚠️  Could not resize image: {e}")
        return image_path


def download_image(img_url, post_id=None):
    """Download an image and return local path, always resize for Kindle"""
    if not DOWNLOAD_IMAGES:
        return img_url
    
    if not is_valid_url(img_url):
        print(f"    ⚠️  Skipping invalid URL: {img_url[:50]}")
        return img_url
    
    # Create URL hash for filename
    url_hash = hashlib.md5(img_url.encode()).hexdigest()[:12]
    
    # Check if already downloaded
    if img_url in downloaded_images:
        return downloaded_images[img_url]
    
    # Try to find existing file
    for ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg']:
        test_path = os.path.join(IMAGES_DIR, f"img_{url_hash}.{ext}")
        if os.path.exists(test_path):
            downloaded_images[img_url] = f"images/img_{url_hash}.{ext}"
            return f"images/img_{url_hash}.{ext}"
    
    try:
        print(f"    📥 Downloading: {img_url[:80]}...")
        
        # Add random delay
        time.sleep(random.uniform(0.5, 1.5))
        
        # Fetch image
        response = fetch_with_retry(img_url, use_proxy=USE_PROXY, is_image=True)
        
        if response and response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            
            if 'image' in content_type:
                # Save original image temporarily
                filename = f"img_{url_hash}.jpg"  # Always save as JPG after processing
                local_path = os.path.join(IMAGES_DIR, filename)
                
                with open(local_path, 'wb') as f:
                    f.write(response.content)
                
                # Always resize and compress for Kindle
                print(f"      🔄 Processing for Kindle (default width: {KINDLE_IMAGE_WIDTH}px)...")
                final_path = resize_image_for_kindle(local_path)
                
                downloaded_images[img_url] = f"images/{os.path.basename(final_path)}"
                file_size = os.path.getsize(final_path) / 1024
                print(f"    ✅ Saved: {os.path.basename(final_path)} ({file_size:.1f} KB)")
                return f"images/{os.path.basename(final_path)}"
            else:
                print(f"    ⚠️  Not an image: {content_type}")
                return img_url
        else:
            print(f"    ❌ Failed: HTTP {response.status_code if response else 'No response'}")
            return img_url
            
    except Exception as e:
        print(f"    ❌ Error: {str(e)[:50]}")
        return img_url


def process_text_images(text, post_id=None):
    """
    Process images in text and return markdown with local image references.
    Preserves ALL text content while adding images.
    """
    if not INCLUDE_IMAGES or not text:
        return text if text else ""
    
    img_urls = extract_image_urls(text)
    
    if not img_urls:
        return text
    
    # Process each image URL and replace in text
    for img_url in img_urls:
        try:
            local_path = download_image(img_url, post_id)
            
            # Create figure with caption and proper HTML for Kindle
            # This preserves text while adding images
            img_markdown = f'\n\n<figure style="margin: 1em 0; text-align: center;">\n'
            img_markdown += f'  <img src="{local_path}" width="{KINDLE_IMAGE_WIDTH}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />\n'
            img_markdown += f'  <figcaption style="font-size: 0.9em; color: #666; margin-top: 0.5em; font-style: italic;">Image from: {img_url[:50]}...</figcaption>\n'
            img_markdown += f'</figure>\n\n'
            
            # Replace the URL with the image markup, but keep surrounding text
            # Use careful replacement to avoid removing text
            text = text.replace(img_url, img_markdown, 1)
        except Exception as e:
            print(f"    ⚠️  Could not process image {img_url[:50]}: {e}")
            continue
    
    return text


def process_reddit_gallery(post):
    """Process Reddit gallery posts with proper text preservation"""
    if not INCLUDE_IMAGES:
        return ""
    
    gallery_data = post.get('gallery_data')
    media_metadata = post.get('media_metadata')
    
    if not gallery_data or not media_metadata:
        return ""
    
    content = ["\n\n## 📸 Gallery Images\n"]
    
    for idx, item in enumerate(gallery_data.get('items', []), 1):
        media_id = item.get('media_id')
        if media_id and media_id in media_metadata:
            media = media_metadata[media_id]
            
            # Get image URL from different possible locations
            img_url = None
            if 's' in media and 'u' in media['s']:
                img_url = media['s']['u']
            elif 'p' in media and len(media['p']) > 0:
                img_url = media['p'][0].get('u')
            
            if img_url:
                # Clean up URL
                img_url = img_url.replace('&amp;', '&')
                if img_url.startswith('//'):
                    img_url = 'https:' + img_url
                
                # Get caption (this is the text we need to preserve!)
                caption = media.get('caption', "")
                
                # If no caption, provide a default but still preserve empty space
                if not caption:
                    caption = f"Gallery image {idx}"
                
                # Download and embed
                local_path = download_image(img_url, post.get('id'))
                
                # Preserve caption text with proper formatting
                content.append(f'\n<figure style="margin: 1em 0; text-align: center;">\n')
                content.append(f'  <img src="{local_path}" width="{KINDLE_IMAGE_WIDTH}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />\n')
                content.append(f'  <figcaption style="font-size: 0.9em; color: #666; margin-top: 0.5em; font-style: italic;">📷 {caption}</figcaption>\n')
                content.append(f'</figure>\n')
    
    return "".join(content)


def fetch_with_retry(url, max_retries=3, use_proxy=True, is_image=False):
    """Fetch URL with retry logic and proxy fallback"""
    global current_proxy_index
    
    # Skip proxy for local files
    if url.startswith('file://') or url.startswith('images/') or url.startswith('http://localhost'):
        use_proxy = False
    
    # Custom headers for images
    headers = session.headers.copy()
    if is_image:
        headers['Accept'] = 'image/webp,image/apng,image/*,*/*;q=0.8'
        headers['Referer'] = 'https://www.reddit.com/'
    
    for attempt in range(max_retries):
        try:
            final_url = url
            if use_proxy and USE_PROXY:
                final_url = get_proxy_url(url, current_proxy_index)
            
            response = session.get(final_url, timeout=30, headers=headers)
            
            if response.status_code == 200:
                return response
            elif response.status_code in [403, 429]:
                if not is_image or attempt > 0:
                    print(f"  Blocked (status {response.status_code})")
                switch_proxy()
                time.sleep(2)
                continue
            else:
                response.raise_for_status()
                
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = (attempt + 1) * 2
                if not is_image:
                    print(f"  Retry {attempt + 1}/{max_retries} in {wait_time}s")
                time.sleep(wait_time)
                if attempt > 0:
                    switch_proxy()
            else:
                if attempt >= max_retries - 1:
                    raise
                return None
    
    return None


def fetch_nested_comments(post_id, limit_comments=None, depth=1, current_depth=1):
    """Fetch comments recursively with depth control"""
    if current_depth > depth:
        return []
    
    url = f"https://www.reddit.com/comments/{post_id}.json"
    time.sleep(random.uniform(1, 2))
    
    try:
        response = fetch_with_retry(url, use_proxy=True)
        if response is None:
            return []
    except Exception as e:
        print(f"  Error fetching comments: {e}")
        return []
    
    try:
        data = response.json() if hasattr(response, 'json') else response
    except:
        return []
    
    if not data or len(data) < 2:
        return []
    
    comments_data = data[1]["data"]["children"]
    comments = []
    comment_count = 0
    
    for item in comments_data:
        if item.get("kind") != "t1":
            continue
        
        comment = item["data"]
        body = comment.get("body", "")
        
        if body in ["[deleted]", "[removed]"]:
            continue
        
        # Process images in comment - preserve text
        if body:
            body = process_text_images(body, post_id)
        
        comment_obj = {
            "author": comment.get("author", "unknown"),
            "body": body if body else "*[No text content]*",
            "depth": current_depth,
            "score": comment.get("score", 0),
            "replies": []
        }
        
        # Fetch replies
        if current_depth < depth and comment.get("replies"):
            replies_data = comment["replies"]
            if isinstance(replies_data, dict) and replies_data.get("data"):
                for reply_item in replies_data["data"]["children"]:
                    if reply_item.get("kind") != "t1":
                        continue
                    
                    reply = reply_item["data"]
                    reply_body = reply.get("body", "")
                    
                    if reply_body in ["[deleted]", "[removed]"]:
                        continue
                    
                    if reply_body:
                        reply_body = process_text_images(reply_body, post_id)
                    
                    reply_obj = {
                        "author": reply.get("author", "unknown"),
                        "body": reply_body if reply_body else "*[No text content]*",
                        "depth": current_depth + 1,
                        "score": reply.get("score", 0),
                        "replies": []
                    }
                    
                    if current_depth + 1 < depth:
                        reply_obj["replies"] = fetch_deeper_replies(reply, current_depth + 1, depth)
                    
                    comment_obj["replies"].append(reply_obj)
        
        comments.append(comment_obj)
        comment_count += 1
        
        if limit_comments and comment_count >= limit_comments:
            break
    
    comments.sort(key=lambda x: x.get("score", 0), reverse=True)
    return comments


def fetch_deeper_replies(comment_data, current_depth, max_depth):
    """Helper function to recursively fetch deeper replies"""
    replies = []
    
    if current_depth >= max_depth:
        return replies
    
    if not comment_data.get("replies"):
        return replies
    
    replies_data = comment_data["replies"]
    if not isinstance(replies_data, dict) or not replies_data.get("data"):
        return replies
    
    for reply_item in replies_data["data"]["children"]:
        if reply_item.get("kind") != "t1":
            continue
        
        reply = reply_item["data"]
        reply_body = reply.get("body", "")
        
        if reply_body in ["[deleted]", "[removed]"]:
            continue
        
        if reply_body:
            reply_body = process_text_images(reply_body)
        
        reply_obj = {
            "author": reply.get("author", "unknown"),
            "body": reply_body if reply_body else "*[No text content]*",
            "depth": current_depth + 1,
            "score": reply.get("score", 0),
            "replies": []
        }
        
        if current_depth + 1 < max_depth:
            reply_obj["replies"] = fetch_deeper_replies(reply, current_depth + 1, max_depth)
        
        replies.append(reply_obj)
    
    return replies


def format_nested_comment(comment):
    """Format a comment and its nested replies, preserving ALL text"""
    lines = []
    header_level = min(comment["depth"] + 2, 6)
    header = "#" * header_level
    asterisks = "*" * comment["depth"]
    
    lines.append(f"\n\n{header} {asterisks} u/{comment['author']} (score: {comment['score']})")
    
    # Add the comment body - this preserves ALL text content
    if comment['body']:
        lines.append('\n')
        lines.append(comment['body'])
    else:
        lines.append('\n*[Empty comment]*')
    
    lines.append("")
    
    for reply in comment.get("replies", []):
        lines.append(format_nested_comment(reply))
    
    return "\n".join(lines)


def fetch_top_comments_flat(post_id):
    """Fetch top-level comments only"""
    url = f"https://www.reddit.com/comments/{post_id}.json"
    time.sleep(random.uniform(1, 2))
    
    try:
        response = fetch_with_retry(url, use_proxy=True)
        if response is None:
            return []
    except Exception as e:
        print(f"  Error fetching comments: {e}")
        return []
    
    try:
        data = response.json()
    except:
        return []
    
    comments_raw = data[1]["data"]["children"]
    comments = []
    
    for c in comments_raw:
        if c.get("kind") != "t1":
            continue
        
        comment = c["data"]
        body = comment.get("body", "")
        
        if body in ["[deleted]", "[removed]"]:
            continue
        
        if body:
            body = process_text_images(body, post_id)
        
        comments.append({
            "author": comment.get("author", "unknown"),
            "body": body if body else "*[No text content]*",
            "score": comment.get("score", 0),
        })
    
    comments.sort(key=lambda x: x.get("score", 0), reverse=True)
    return comments[:TOP_COMMENTS_PER_POST]


def extract_post_content(post):
    """
    Extract and format ALL content from a post, including all text descriptions.
    This is the key function - it preserves ALL text content.
    """
    content = []
    
    title = post.get("title", "Untitled")
    subreddit = post.get("subreddit", "unknown")
    score = post.get("score", 0)
    num_comments = post.get("num_comments", 0)
    permalink = post.get("permalink", "")
    selftext = post.get("selftext", "")
    post_id = post.get("id")
    url = post.get("url", "")
    author = post.get("author", "unknown")
    
    # Post header (always include)
    content.append(f"# r/{subreddit} | {title}")
    content.append(f"\n\n**Posted by:** u/{author}")
    content.append(f"\n\n**Score:** {score} | **Comments:** {num_comments}")
    content.append(f"\n\n**Link:** https://reddit.com{permalink}\n")
    
    # ===== POST BODY TEXT - CRITICAL SECTION =====
    # This must preserve ALL text content
    post_body = ""
    
    # Priority 1: Selftext (normal text posts)
    if selftext and selftext.strip():
        post_body = selftext.strip()
        print(f"    📝 Found selftext: {len(post_body)} chars")
    
    # Priority 2: URL/link posts - include the link as text
    elif url and is_valid_url(url) and 'reddit.com' not in url.lower():
        post_body = f"**Shared link:** [{url}]({url})"
        print(f"    🔗 Link post: {url[:60]}...")
    
    # Process images within the post body while preserving text
    if post_body:
        # Process images - this adds images but KEEPS the text
        post_body = process_text_images(post_body, post_id)
        content.append(f"\n\n> {post_body}\n\n")
    else:
        # If no body text, still add a placeholder
        content.append(f"\n\n*[No additional text content in this post]*\n\n")
    
    # ===== GALLERY POSTS - Preserve gallery descriptions =====
    if post.get('gallery_data') and post.get('media_metadata'):
        # Check for gallery description (important text to preserve!)
        gallery_description = post.get('gallery_description', '')
        if gallery_description and gallery_description.strip():
            gallery_description = process_text_images(gallery_description.strip(), post_id)
            content.append(f"\n## Gallery Description\n\n{gallery_description}\n")
            print(f"    🖼️  Gallery description: {len(gallery_description)} chars")
        
        # Add gallery images with their captions
        content.append(process_reddit_gallery(post))
    
    # ===== SINGLE IMAGE POSTS =====
    # Handle single image posts while preserving any surrounding text
    if url and is_valid_url(url) and not post_body:
        url_lower = url.lower()
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.webpd', '.bmp']
        
        if any(url_lower.endswith(ext) for ext in image_extensions):
            local_path = download_image(url, post_id)
            content.append(f'\n<figure style="margin: 1em 0; text-align: center;">\n')
            content.append(f'  <img src="{local_path}" width="{KINDLE_IMAGE_WIDTH}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />\n')
            content.append(f'  <figcaption style="font-size: 0.9em; color: #666; margin-top: 0.5em; font-style: italic;">📷 Single image post</figcaption>\n')
            content.append(f'</figure>\n')
        elif 'imgur.com' in url_lower or 'i.redd.it' in url_lower:
            local_path = download_image(url, post_id)
            content.append(f'\n<figure style="margin: 1em 0; text-align: center;">\n')
            content.append(f'  <img src="{local_path}" width="{KINDLE_IMAGE_WIDTH}" style="max-width: 100%; height: auto; display: block; margin: 0 auto;" />\n')
            content.append(f'  <figcaption style="font-size: 0.9em; color: #666; margin-top: 0.5em; font-style: italic;">📷 Shared image</figcaption>\n')
            content.append(f'</figure>\n')
    
    # ===== COMMENTS SECTION =====
    content.append("\n\n## 💬 Comments\n")
    
    try:
        if INCLUDE_NESTED_COMMENTS and COMMENT_DEPTH > 1:
            nested_comments = fetch_nested_comments(
                post_id,
                limit_comments=TOP_COMMENTS_PER_POST,
                depth=COMMENT_DEPTH
            )
            
            if nested_comments:
                for comment in nested_comments:
                    content.append(format_nested_comment(comment))
            else:
                content.append("\n*No comments found.*\n")
        else:
            comments = fetch_top_comments_flat(post_id)
            if comments:
                for comment in comments:
                    content.append(f"\n\n### * u/{comment['author']} (score: {comment['score']})")
                    content.append('\n')
                    content.append(comment['body'])
                    content.append("")
            else:
                content.append("\n*No comments found.*\n")
    except Exception as e:
        content.append(f"\n*Failed to load comments: {e}*\n")
    
    content.append("\n\n---\n")
    
    return "\n".join(content)


def collect_posts():
    """Collect posts from configured subreddits"""
    all_posts = []
    
    for subreddit_name in SUBREDDITS:
        print(f"\n📁 Fetching from r/{subreddit_name}...")
        
        endpoints = [
            f"https://www.reddit.com/r/{subreddit_name}/top.json?t=day&limit={POST_LIMIT_PER_SUBREDDIT}",
            f"https://www.reddit.com/r/{subreddit_name}.json?limit={POST_LIMIT_PER_SUBREDDIT}",
        ]
        
        post_data = None
        
        for endpoint in endpoints:
            if all_posts:
                time.sleep(random.uniform(1, 2))
            
            try:
                print(f"  Trying: {endpoint.split('?')[0]}...")
                response = fetch_with_retry(endpoint, use_proxy=True)
                
                if response and response.status_code == 200:
                    data = response.json()
                    posts = data["data"]["children"]
                    
                    if posts:
                        post_data = posts
                        print(f"  ✅ Found {len(posts)} posts")
                        break
                    else:
                        print(f"  ⚠️  No posts")
                else:
                    print(f"  ❌ Failed: {response.status_code if response else 'No response'}")
                    
            except Exception as e:
                print(f"  ❌ Error: {str(e)[:80]}")
                continue
        
        if not post_data:
            print(f"  ❌ Could not fetch r/{subreddit_name}")
            continue
        
        for p in post_data:
            post = p["data"]
            
            if post.get("score", 0) < MIN_SCORE:
                continue
            
            if post.get("stickied"):
                continue
            
            all_posts.append(post)
    
    return all_posts


def generate_markdown(posts):
    """Generate markdown content from posts - preserves ALL text"""
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    lines = []
    lines.append(f"# 📚 Reddit Digest — {date_str}\n")
    lines.append("*Generated automatically for Kindle reading*\n")
    
    if INCLUDE_NESTED_COMMENTS and COMMENT_DEPTH > 1:
        lines.append(f"*Comment depth: Level {COMMENT_DEPTH}*\n")
    
    if USE_PROXY:
        lines.append(f"*Using CORS proxies*\n")
    
    if INCLUDE_IMAGES:
        lines.append(f"*Images included (optimized for Kindle: {KINDLE_IMAGE_WIDTH}px width)*")
        if DOWNLOAD_IMAGES:
            lines.append(f" *({len(downloaded_images)} downloaded and optimized)*\n")
        else:
            lines.append("\n")
    
    lines.append("\n---\n")
    
    print(f"\n📝 Generating {len(posts)} posts...")
    
    for i, post in enumerate(posts, 1):
        try:
            title = post.get('title', 'Untitled')[:60]
            print(f"  {i}/{len(posts)}: {title}...")
            
            # Extract ALL content including all text
            post_content = extract_post_content(post)
            lines.append(post_content)
            
            time.sleep(random.uniform(0.3, 0.7))
        except Exception as e:
            print(f"  ❌ Failed: {e}")
            import traceback
            traceback.print_exc()
            lines.append(f"\n*Failed to process post: {e}*\n\n---\n")
    
    markdown_content = "\n".join(lines)
    
    # Clean up any malformed references but preserve text
    markdown_content = re.sub(r'!\[.*?\]\([^)]*\|[^)]*\)', '', markdown_content)
    
    # Ensure we don't have empty figure tags
    markdown_content = re.sub(r'<figure[^>]*>\s*</figure>', '', markdown_content)
    
    md_path = os.path.join(OUTPUT_DIR, f"reddit_digest_{date_str}.md")
    
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    
    print(f"\n  ✅ Markdown saved: {md_path}")
    print(f"  📄 Markdown size: {os.path.getsize(md_path) / 1024:.1f} KB")
    
    if DOWNLOAD_IMAGES and downloaded_images:
        print(f"  📸 Processed {len(downloaded_images)} images (all optimized for Kindle)")
    
    return md_path


def convert_to_epub(md_path):
    """Convert markdown to EPUB using pandoc - preserves ALL content"""
    epub_path = md_path.replace(".md", ".epub")
    
    # Check pandoc version
    try:
        result = subprocess.run(['pandoc', '--version'], capture_output=True, text=True)
        version_match = re.search(r'pandoc (\d+\.\d+)', result.stdout)
        pandoc_version = version_match.group(1) if version_match else '0.0'
        print(f"  Pandoc version: {pandoc_version}")
    except:
        pandoc_version = '0.0'
        print(f"  ⚠️  Could not detect pandoc version")
    
    # Build basic pandoc command (remove problematic flags)
    cmd = [
        "pandoc",
        md_path,
        "-o",
        epub_path,
        "--toc",
        "--metadata",
        "title=Reddit Digest",
        "--standalone",
    ]
    
    # Add resource path for images
    if DOWNLOAD_IMAGES and os.path.exists(IMAGES_DIR):
        cmd.extend(["--resource-path", OUTPUT_DIR])
    
    # Add embed-resources only for newer pandoc versions (>= 2.10)
    # Remove --wrap=preserve and --markdown-headings as they cause issues
    pandoc_major = float(pandoc_version.split('.')[0]) if pandoc_version != '0.0' else 0
    pandoc_minor = float(pandoc_version.split('.')[1]) if len(pandoc_version.split('.')) > 1 else 0
    
    if pandoc_major >= 3 or (pandoc_major == 2 and pandoc_minor >= 10):
        cmd.append("--embed-resources")
        print(f"\n📚 Converting to EPUB (embedding images and preserving text)...")
    else:
        print(f"\n📚 Converting to EPUB...")
    
    try:
        # Run pandoc and capture output to see if there are issues
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"  ✅ EPUB saved: {epub_path}")
            
            # Check EPUB size
            epub_size = os.path.getsize(epub_path) / (1024 * 1024)
            print(f"  📊 EPUB size: {epub_size:.2f} MB")
            
            if epub_size > 25:
                print(f"  ⚠️  Warning: EPUB exceeds 25MB email limit!")
            
            return epub_path
        else:
            print(f"  ⚠️  Pandoc had issues: {result.stderr[:200]}")
            print(f"  Trying fallback conversion without --embed-resources...")
            
            # Fallback: try without --embed-resources
            fallback_cmd = [c for c in cmd if c != "--embed-resources"]
            result2 = subprocess.run(fallback_cmd, capture_output=True, text=True)
            
            if result2.returncode == 0:
                print(f"  ✅ EPUB saved (fallback): {epub_path}")
                return epub_path
            else:
                print(f"  ❌ Fallback also failed: {result2.stderr[:200]}")
                print(f"  \n  💡 Manual conversion command:")
                print(f"     pandoc {md_path} -o {epub_path} --toc")
                raise subprocess.CalledProcessError(result2.returncode, fallback_cmd)
            
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Pandoc failed: {e}")
        print(f"  EPUB not created, but markdown is available at: {md_path}")
        raise


def send_to_kindle(file_path):
    """Send EPUB to Kindle email"""
    print(f"\n📧 Sending to Kindle...")
    
    # Check file size before sending
    file_size = os.path.getsize(file_path) / (1024 * 1024)
    if file_size > 25:
        print(f"  ⚠️  Warning: File is {file_size:.2f} MB, which exceeds Kindle's 25MB limit!")
        print(f"  The email may fail. Consider reducing IMAGE_QUALITY or KINDLE_IMAGE_WIDTH in .env")
    
    msg = EmailMessage()
    msg["Subject"] = "Reddit Digest"
    msg["From"] = KINDLE["sender_email"]
    msg["To"] = KINDLE["kindle_email"]
    msg.set_content(f"Your Reddit digest is ready. Size: {file_size:.2f} MB")
    
    with open(file_path, "rb") as f:
        file_data = f.read()
    
    filename = os.path.basename(file_path)
    msg.add_attachment(file_data, maintype="application", subtype="octet-stream", filename=filename)
    
    try:
        with smtplib.SMTP(KINDLE["smtp_server"], KINDLE["smtp_port"]) as smtp:
            smtp.starttls()
            smtp.login(KINDLE["sender_email"], KINDLE["sender_password"])
            smtp.send_message(msg)
        print("  ✅ Email sent!")
    except Exception as e:
        print(f"  ❌ Failed to send: {e}")
        print(f"  EPUB saved locally: {file_path}")


def main():
    """Main execution function"""
    print("=" * 60)
    print("📚 Reddit Digest Generator v2.0 - Kindle Optimized")
    print("=" * 60)
    print(f"Subreddits: {', '.join(SUBREDDITS)}")
    print(f"Posts per subreddit: {POST_LIMIT_PER_SUBREDDIT}")
    print(f"Comment depth: {COMMENT_DEPTH if INCLUDE_NESTED_COMMENTS else 'Top-level only'}")
    print(f"Proxies: {'Enabled' if USE_PROXY else 'Disabled'}")
    print(f"Images: {'Enabled' if INCLUDE_IMAGES else 'Disabled'}")
    if INCLUDE_IMAGES and DOWNLOAD_IMAGES:
        print(f"  - Kindle optimization:")
        print(f"    • Max width: {KINDLE_IMAGE_WIDTH}px")
        print(f"    • Quality: {KINDLE_IMAGE_QUALITY}%")
        print(f"    • Max size per image: {KINDLE_MAX_IMAGE_SIZE_MB}MB")
    print()
    
    # Collect posts
    print("📡 Collecting posts...")
    posts = collect_posts()
    print(f"\n✅ Collected {len(posts)} total posts")
    
    if len(posts) == 0:
        print("\n❌ No posts found. Exiting.")
        print("\nTroubleshooting:")
        print("  1. Check subreddit names in .env")
        print("  2. Try USE_PROXY=True in .env")
        print("  3. Wait a few minutes and try again")
        return
    
    # Generate content
    print(f"\n💬 Fetching comments and preserving all text...")
    print("\n📝 Generating markdown...")
    md_path = generate_markdown(posts)
    
    # Convert to EPUB
    print("\n📚 Converting to EPUB...")
    try:
        epub_path = convert_to_epub(md_path)
        
        # Send to Kindle
        if (KINDLE["sender_email"] and KINDLE["sender_password"] and KINDLE["kindle_email"] and
            all(x not in ["not_set@example.com", "not_set@kindle.com", "not_set"] 
                for x in [KINDLE["sender_email"], KINDLE["kindle_email"]])):
            send_to_kindle(epub_path)
        else:
            print("\n⚠️  Email not configured. EPUB saved locally.")
            print(f"   Location: {epub_path}")
    except Exception as e:
        print(f"\n❌ Failed to create EPUB: {e}")
        print(f"   Markdown file is available at: {md_path}")
        print(f"   You can manually convert it using: pandoc {md_path} -o output.epub")
    
    print("\n" + "=" * 60)
    print("✅ Done!")
    print("=" * 60)


if __name__ == "__main__":
    main()