#!/usr/bin/env python3
"""
Comic Alt Text Generator
Automatically generates alt text for comic images using AI vision
"""

import os
import yaml
import base64
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import time
import json
import random
import threading

class ComicAltTextGenerator:
    def __init__(self, api_key: str, model: str = "anthropic/claude-3.5-sonnet"):
        """
        Initialize the generator with OpenRouter API credentials
        
        Args:
            api_key: Your OpenRouter API key
            model: The model to use (default: claude-3.5-sonnet for vision)
        """
        self.api_key = api_key
        self.model = model
        self.base_url = "https://openrouter.ai/api/v1/chat/completions"
        self.comics_file = "../comics.yaml"
        self.comic_files_dir = "../comic_files"
        
        # Supported image formats
        self.supported_formats = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
        # Adaptive base delay (seconds) used between items; adjusted on 429s/success
        self.delay = 0.7
        self.max_delay = 10.0
        self.min_delay = 0.3
        self.delay_increment = 0.3
        self.delay_decay = 0.1

        # Token bucket rate limiter (requests per second)
        self.rps = float(os.getenv("ALT_TEXT_RPS", "1.0"))  # 0.5–1.0 is recommended
        self.bucket_capacity = int(max(1, float(os.getenv("ALT_TEXT_BURST", "2"))))
        self._tokens = self.bucket_capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

        # Retry/backoff settings
        self.max_retries = int(os.getenv("ALT_TEXT_MAX_RETRIES", "6"))
        self.base_backoff = float(os.getenv("ALT_TEXT_BASE_BACKOFF", "0.5"))
        self.jitter_ratio = float(os.getenv("ALT_TEXT_JITTER", "0.3"))

    def load_existing_comics(self) -> List[Dict[str, Any]]:
        """Load existing comics from YAML file"""
        if os.path.exists(self.comics_file):
            try:
                with open(self.comics_file, 'r', encoding='utf-8') as f:
                    comics = yaml.safe_load(f) or []
                    return comics if isinstance(comics, list) else []
            except Exception as e:
                print(f"Error loading existing comics file: {e}")
                return []
        return []
    
    def save_comics(self, comics: List[Dict[str, Any]]) -> None:
        """Save comics list to YAML file"""
        try:
            with open(self.comics_file, 'w', encoding='utf-8') as f:
                yaml.dump(comics, f, default_flow_style=False, allow_unicode=True, width=1000)
            print(f"Saved {len(comics)} comics to {self.comics_file}")
        except Exception as e:
            print(f"Error saving comics file: {e}")
    
    def encode_image_to_base64(self, image_path: str) -> str:
        """Encode image to base64 string"""
        try:
            with open(image_path, "rb") as image_file:
                encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                return encoded_string
        except Exception as e:
            print(f"Error encoding image {image_path}: {e}")
            return ""
    
    def get_image_mime_type(self, file_path: str) -> str:
        """Get MIME type based on file extension"""
        ext = Path(file_path).suffix.lower()
        mime_types = {
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.gif': 'image/gif',
            '.webp': 'image/webp'
        }
        return mime_types.get(ext, 'image/png')
    
    def _refill_tokens(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        tokens_to_add = int(elapsed * self.rps)
        if tokens_to_add > 0:
            self._tokens = min(self.bucket_capacity, self._tokens + tokens_to_add)
            self._last_refill = now

    def _acquire_token(self) -> float:
        """Acquire a token from the bucket. Returns sleep time taken (for logging)."""
        slept = 0.0
        with self._lock:
            self._refill_tokens()
            if self._tokens > 0:
                self._tokens -= 1
                return slept
        # Need to wait for next token
        while True:
            with self._lock:
                self._refill_tokens()
                if self._tokens > 0:
                    self._tokens -= 1
                    return slept
            wait_time = max(0.01, 1.0 / max(self.rps, 0.001))
            time.sleep(wait_time)
            slept += wait_time

    def request_with_backoff(self, json_payload: Dict[str, Any]) -> Tuple[Optional[requests.Response], Optional[int], float]:
        """
        Perform POST with exponential backoff, jitter, token-bucket limiting, and Retry-After support.
        Returns: (response, retry_count, total_sleep)
        """
        total_sleep = 0.0
        retries = 0
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        while True:
            # Token bucket gating
            slept = self._acquire_token()
            if slept > 0:
                print(f"[rate-limit] token wait: slept {slept:.2f}s before request")
                total_sleep += slept
            try:
                resp = requests.post(self.base_url, headers=headers, json=json_payload, timeout=60)
                # Honor Retry-After and status codes
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    retry_after = resp.headers.get("Retry-After")
                    if retry_after:
                        try:
                            sleep_secs = float(retry_after)
                        except ValueError:
                            sleep_secs = self.base_backoff * (2 ** retries)
                    else:
                        sleep_secs = self.base_backoff * (2 ** retries)
                    # add jitter
                    sleep_secs += random.uniform(0, sleep_secs * self.jitter_ratio)
                    print(f"[backoff] status={resp.status_code} retry={retries} sleep={sleep_secs:.2f}s")
                    total_sleep += sleep_secs
                    time.sleep(min(sleep_secs, 60))
                    retries += 1
                    if retries > self.max_retries:
                        return resp, retries, total_sleep
                    # Adaptive delay increase on 429
                    if resp.status_code == 429:
                        self.delay = min(self.max_delay, self.delay + self.delay_increment)
                    continue
                # Success or non-retryable error
                return resp, retries, total_sleep
            except requests.exceptions.RequestException as e:
                sleep_secs = self.base_backoff * (2 ** retries)
                sleep_secs += random.uniform(0, sleep_secs * self.jitter_ratio)
                print(f"[error] {e.__class__.__name__}: retry={retries} sleep={sleep_secs:.2f}s")
                total_sleep += sleep_secs
                time.sleep(min(sleep_secs, 60))
                retries += 1
                if retries > self.max_retries:
                    return None, retries, total_sleep

    def generate_alt_text(self, image_path: str) -> Dict[str, str]:
        """
        Generate alt text for a comic image using AI vision
        
        Args:
            image_path: Path to the comic image
            
        Returns:
            Dictionary with title and caption
        """
        try:
            # Encode image
            base64_image = self.encode_image_to_base64(image_path)
            if not base64_image:
                return {"title": "Error", "caption": "Could not process image"}
            
            mime_type = self.get_image_mime_type(image_path)
            
            # Prepare the prompt for comic alt text generation
            prompt = """You are an expert at creating accessible alt text for webcomics. 

Analyze this comic image and provide:
1. A brief, descriptive title (2-4 words)
2. A detailed caption describing the comic panels, characters, dialogue, and visual elements

Format your response as JSON:
{
    "title": "Brief descriptive title",
    "caption": "Panel 1: [description]. Panel 2: [description]. etc."
}

Focus on:
- Panel-by-panel description
- Character descriptions and actions
- Dialogue and text
- Visual gags or important details
- Overall narrative flow

Keep descriptions clear and concise but comprehensive enough for screen readers."""

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_image}"}},
                        ],
                    }
                ],
                "max_tokens": 1000,
                "temperature": 0.3,
            }

            resp, retries, slept = self.request_with_backoff(payload)
            if resp is None:
                print(f"API request failed after retries: {image_path}")
                return {"title": "Error", "caption": "Failed to generate description"}
            if resp.status_code >= 400:
                print(f"API returned error {resp.status_code} after {retries} retries, total sleep {slept:.2f}s")
                return {"title": "Error", "caption": f"API error {resp.status_code}"}

            result = resp.json()
            # OpenRouter returns message.content sometimes as string or list; normalize
            content = result['choices'][0]['message']['content']

            # Try to parse JSON response
            try:
                parsed_content = json.loads(content)
                # Adaptive delay decay on success
                self.delay = max(self.min_delay, self.delay - self.delay_decay)
                return {
                    "title": parsed_content.get("title", "Comic"),
                    "caption": parsed_content.get("caption", "Comic description unavailable"),
                }
            except json.JSONDecodeError:
                # Fallback: use the content as caption
                self.delay = max(self.min_delay, self.delay - self.delay_decay)
                return {
                    "title": "Comic",
                    "caption": content.strip(),
                }
            
        except requests.exceptions.RequestException as e:
            print(f"API request error for {image_path}: {e}")
            return {"title": "Error", "caption": "Failed to generate description"}
        except Exception as e:
            print(f"Unexpected error processing {image_path}: {e}")
            return {"title": "Error", "caption": "Failed to process image"}

    def get_comic_files(self) -> List[str]:
        """Get all comic image files from the comic_files directory"""
        if not os.path.exists(self.comic_files_dir):
            print(f"Directory {self.comic_files_dir} not found!")
            return []
        
        comic_files = []
        for file in os.listdir(self.comic_files_dir):
            file_path = os.path.join(self.comic_files_dir, file)
            if os.path.isfile(file_path) and Path(file).suffix.lower() in self.supported_formats:
                comic_files.append(file)
        
        return sorted(comic_files)
    
    def _save_progress(self, comics: List[Dict[str, Any]]) -> None:
        """Persist progress frequently to avoid losing work."""
        try:
            self.save_comics(comics)
        except Exception as e:
            print(f"Warning: failed to persist progress: {e}")

    def process_all_comics(self, delay: float = 1.0, max_per_run: Optional[int] = None) -> None:
        """
        Process all comic files and generate alt text
        
        Args:
            delay: Base delay between API calls to avoid rate limiting (will adapt)
            max_per_run: Optional cap on how many items to process this run
        """

        # Load existing comics
        existing_comics = self.load_existing_comics()
        existing_filenames = {comic.get('filename') for comic in existing_comics}
        
        # Get all comic files
        comic_files = self.get_comic_files()
        
        if not comic_files:
            print("No comic files found!")
            return
        
        print(f"Found {len(comic_files)} comic files")
        print(f"Already processed: {len(existing_filenames)}")
        
        # Process each comic file
        processed_count = 0
        for i, filename in enumerate(comic_files, 1):
            if max_per_run is not None and processed_count >= max_per_run:
                print(f"Reached max_per_run={max_per_run}, stopping early.")
                break

            print(f"\nProcessing {i}/{len(comic_files)}: {filename}")
            
            # Skip if already processed
            if filename in existing_filenames:
                print(f"  → Skipping (already processed)")
                continue
            
            # Generate alt text
            image_path = os.path.join(self.comic_files_dir, filename)
            result = self.generate_alt_text(image_path)
            
            # Add to comics list
            comic_entry = {
                'filename': filename,
                'title': result['title'],
                'caption': result['caption']
            }
            
            existing_comics.append(comic_entry)
            processed_count += 1
            # Persist progress frequently
            if processed_count % 5 == 0:
                print("Saving progress…")
                self._save_progress(existing_comics)
            
            print(f"  → Title: {result['title']}")
            print(f"  → Caption: {result['caption'][:100]}...")
            
            # Rate limiting delay (adaptive)
            effective_delay = max(self.min_delay, delay, self.delay)
            if i < len(comic_files):
                print(f"[sleep] delaying {effective_delay:.2f}s (adaptive base: {self.delay:.2f}s)")
                time.sleep(effective_delay)
        
        # Final save
        if processed_count > 0:
            self.save_comics(existing_comics)
            print(f"\n✅ Completed! Processed {processed_count} new comics")
        else:
            print(f"\n✅ All comics already processed!")

def main():
    """Main function to run the comic alt text generator"""
    
    # Configuration
    API_KEY = os.getenv('OPENROUTER_API_KEY')
    if not API_KEY:
        print("Error: Please set your OPENROUTER_API_KEY environment variable")
        print("You can get an API key from: https://openrouter.ai/")
        return
    
    # You can also use other vision models:
    # MODEL = "anthropic/claude-3-haiku"  # Faster, cheaper
    # MODEL = "openai/gpt-4-vision-preview"
    # MODEL = "google/gemini-pro-vision"
    # MODEL = "anthropic/claude-3.5-sonnet"  # Best quality
    MODEL = "openrouter/horizon-beta"
    
    # Initialize generator
    generator = ComicAltTextGenerator(api_key=API_KEY, model=MODEL)
    
    # Process all comics
    print("🎨 Comic Alt Text Generator Starting...")
    print(f"📁 Looking for comics in: {generator.comic_files_dir}")
    print(f"💾 Saving results to: {generator.comics_file}")
    print(f"🤖 Using model: {MODEL}")
    print("-" * 50)
    
    # Allow limiting work per run via env var
    max_per_run_env = os.getenv("ALT_TEXT_MAX_PER_RUN")
    max_per_run = int(max_per_run_env) if max_per_run_env else None
    generator.process_all_comics(delay=1.5, max_per_run=max_per_run)


if __name__ == "__main__":
    main()