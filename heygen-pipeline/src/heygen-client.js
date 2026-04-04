/**
 * HeyGen API Client
 * Handles all communication with the HeyGen API
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class HeyGenClient {
  constructor(apiKey) {
    this.apiKey = apiKey;
    this.baseUrl = 'https://api.heygen.com';
  }

  /**
   * Create a new HeyGen client from config file
   */
  static fromConfig(configPath) {
    const config = loadConfig(configPath);
    const apiKey = fs.readFileSync(
      config.heygen.api_key_path.replace('~', process.env.HOME),
      'utf8'
    ).trim();
    return new HeyGenClient(apiKey);
  }

  /**
   * Get available avatars
   */
  async getAvatars() {
    const response = await fetch(`${this.baseUrl}/v2/avatars`, {
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      }
    });
    
    if (!response.ok) {
      throw new Error(`Failed to get avatars: ${response.status} ${response.statusText}`);
    }
    
    return await response.json();
  }

  /**
   * Get available voices
   */
  async getVoices() {
    const response = await fetch(`${this.baseUrl}/v2/voices`, {
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      }
    });
    
    if (!response.ok) {
      throw new Error(`Failed to get voices: ${response.status} ${response.statusText}`);
    }
    
    return await response.json();
  }

  /**
   * Create a video from text/script
   */
  async createVideo(options) {
    const {
      script,
      avatarId,
      voiceId,
      width = 1920,
      height = 1080,
      background = null,
      caption = false
    } = options;

    const payload = {
      video_inputs: [{
        character: {
          type: 'avatar',
          avatar_id: avatarId,
          avatar_style: 'normal'
        },
        voice: {
          type: 'text',
          input_text: script,
          voice_id: voiceId,
          speed: 1.0
        },
        background: background || {
          type: 'color',
          value: '#FFFFFF'
        }
      }],
      dimension: {
        width: width,
        height: height
      },
      caption: caption
    };

    const response = await fetch(`${this.baseUrl}/v2/video/generate`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Failed to create video: ${response.status} ${error}`);
    }

    return await response.json();
  }

  /**
   * Get video status and URL
   */
  async getVideoStatus(videoId) {
    const response = await fetch(`${this.baseUrl}/v1/video_status.get?video_id=${videoId}`, {
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      }
    });

    if (!response.ok) {
      throw new Error(`Failed to get video status: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Delete a video
   */
  async deleteVideo(videoId) {
    const response = await fetch(`${this.baseUrl}/v1/video.delete`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ video_id: videoId })
    });

    if (!response.ok) {
      throw new Error(`Failed to delete video: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  }

  /**
   * Create template-based video
   */
  async createFromTemplate(templateId, variables) {
    const payload = {
      template_id: templateId,
      variables: variables
    };

    const response = await fetch(`${this.baseUrl}/v2/video/generate`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${this.apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`Failed to create template video: ${response.status} ${response.statusText}`);
    }

    return await response.json();
  }
}

/**
 * Load configuration from YAML file
 */
export function loadConfig(configPath) {
  const yaml = fs.readFileSync(configPath, 'utf8');
  const config = {};
  
  // Simple YAML parser for our config format
  let currentSection = null;
  let currentSubsection = null;
  let inList = false;
  let listItems = [];
  
  for (const line of yaml.split('\n')) {
    const trimmed = line.trim();
    
    // Skip comments and empty lines
    if (!trimmed || trimmed.startsWith('#')) continue;
    
    // Section header
    if (trimmed.endsWith(':') && !trimmed.includes(' ')) {
      currentSection = trimmed.slice(0, -1);
      config[currentSection] = {};
      continue;
    }
    
    // List item
    if (trimmed.startsWith('- ')) {
      if (!Array.isArray(config[currentSection])) {
        // Convert object to array if we hit a list
        if (Object.keys(config[currentSection]).length === 0) {
          config[currentSection] = [];
        }
      }
      
      if (Array.isArray(config[currentSection])) {
        const item = trimmed.slice(2);
        if (item.includes(': ')) {
          const [key, value] = item.split(': ').map(s => s.trim());
          if (!listItems.length || listItems[listItems.length - 1][key]) {
            listItems.push({});
          }
          listItems[listItems.length - 1][key] = value.replace(/^["']|["']$/g, '');
        }
        config[currentSection] = listItems;
      }
      continue;
    }
    
    // Key-value pair
    if (trimmed.includes(': ')) {
      const [key, value] = trimmed.split(': ').map(s => s.trim());
      const cleanValue = value.replace(/^["']|["']$/g, '');
      
      if (currentSection) {
        if (!config[currentSection]) config[currentSection] = {};
        
        // Try to parse as number or boolean
        if (cleanValue === 'true') config[currentSection][key] = true;
        else if (cleanValue === 'false') config[currentSection][key] = false;
        else if (!isNaN(cleanValue) && cleanValue !== '') config[currentSection][key] = Number(cleanValue);
        else config[currentSection][key] = cleanValue;
      }
    }
  }
  
  return config;
}

export default HeyGenClient;
