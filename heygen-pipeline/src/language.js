/**
 * Multi-language Content Support
 * Handles content translation and language-specific settings
 */

import fs from 'fs';
import path from 'path';

export class LanguageManager {
  constructor(config) {
    this.config = config;
    this.translations = new Map();
    this.loadTranslations();
  }

  /**
   * Load translation files if they exist
   */
  loadTranslations() {
    const translationsDir = './translations';
    if (!fs.existsSync(translationsDir)) return;

    const files = fs.readdirSync(translationsDir).filter(f => f.endsWith('.json'));
    
    files.forEach(file => {
      const lang = path.basename(file, '.json');
      const content = JSON.parse(fs.readFileSync(path.join(translationsDir, file), 'utf8'));
      this.translations.set(lang, content);
    });
  }

  /**
   * Get supported languages
   */
  getSupportedLanguages() {
    return this.config.languages?.supported || [
      { code: 'en', name: 'English', default_voice: 'en-US-Standard' },
      { code: 'es', name: 'Spanish', default_voice: 'es-ES-Standard' },
      { code: 'fr', name: 'French', default_voice: 'fr-FR-Standard' }
    ];
  }

  /**
   * Get language configuration
   */
  getLanguageConfig(code) {
    const languages = this.getSupportedLanguages();
    return languages.find(l => l.code === code);
  }

  /**
   * Get voice ID for language
   */
  getVoiceForLanguage(code, voiceType = 'standard') {
    const lang = this.getLanguageConfig(code);
    if (!lang) return null;
    
    // Check for specific voice type
    if (voiceType === 'neural' && lang.neural_voice) {
      return lang.neural_voice;
    }
    
    return lang.default_voice || lang.voice_id;
  }

  /**
   * Get avatar ID for language (for localized avatars)
   */
  getAvatarForLanguage(code, defaultAvatar) {
    const lang = this.getLanguageConfig(code);
    if (!lang) return defaultAvatar;
    
    return lang.default_avatar || defaultAvatar;
  }

  /**
   * Process a script for a specific language
   * Returns language-specific configuration
   */
  processScript(script, targetLanguage, options = {}) {
    const langConfig = this.getLanguageConfig(targetLanguage);
    
    if (!langConfig) {
      throw new Error(`Unsupported language: ${targetLanguage}`);
    }

    return {
      script: script,
      language: targetLanguage,
      voiceId: options.voiceId || this.getVoiceForLanguage(targetLanguage, options.voiceType),
      avatarId: options.avatarId || this.getAvatarForLanguage(targetLanguage, options.defaultAvatar),
      languageName: langConfig.name
    };
  }

  /**
   * Create multi-language variations of a script
   * Returns an array of language configurations
   */
  createMultiLanguageVariations(script, targetLanguages, options = {}) {
    return targetLanguages.map(lang => {
      const processed = this.processScript(script, lang, options);
      return {
        ...processed,
        // Generate a unique identifier for this variation
        variationId: `${options.baseId || 'video'}_${lang}`,
        // Add any client-specific overrides
        client: options.client,
        campaign: options.campaign
      };
    });
  }

  /**
   * Translate text using simple placeholder replacement
   * For real translation, you'd integrate with a translation API
   */
  translateText(text, targetLanguage, placeholders = {}) {
    const translations = this.translations.get(targetLanguage);
    
    if (!translations) {
      console.warn(`No translations found for ${targetLanguage}, using original text`);
      return this.replacePlaceholders(text, placeholders);
    }

    // Simple translation lookup
    let translated = translations[text] || text;
    
    return this.replacePlaceholders(translated, placeholders);
  }

  /**
   * Replace placeholders in text
   */
  replacePlaceholders(text, placeholders) {
    let result = text;
    for (const [key, value] of Object.entries(placeholders)) {
      result = result.replace(new RegExp(`{{${key}}}`, 'g'), value);
    }
    return result;
  }

  /**
   * Generate language-specific filenames
   */
  generateFilename(baseName, language, extension = 'mp4') {
    return `${baseName}_${language}.${extension}`;
  }

  /**
   * Format script with proper pauses for different languages
   * Some languages need different timing
   */
  formatScriptForLanguage(script, language) {
    // Asian languages might need slightly different pacing
    const asianLanguages = ['ja', 'zh', 'ko'];
    
    if (asianLanguages.includes(language)) {
      // Add slight pauses after punctuation for clarity
      return script
        .replace(/。/g, '。 [pause]')
        .replace(/、/g, '、 [shortpause]');
    }
    
    return script;
  }
}

/**
 * Template manager for language-specific video templates
 */
export class TemplateManager {
  constructor(templatesDir = './templates') {
    this.templatesDir = templatesDir;
    this.templates = new Map();
    this.loadTemplates();
  }

  loadTemplates() {
    if (!fs.existsSync(this.templatesDir)) {
      fs.mkdirSync(this.templatesDir, { recursive: true });
      return;
    }

    const files = fs.readdirSync(this.templatesDir).filter(f => f.endsWith('.json'));
    
    files.forEach(file => {
      const name = path.basename(file, '.json');
      const content = JSON.parse(fs.readFileSync(path.join(this.templatesDir, file), 'utf8'));
      this.templates.set(name, content);
    });
  }

  /**
   * Get template by name
   */
  getTemplate(name) {
    return this.templates.get(name);
  }

  /**
   * List available templates
   */
  listTemplates() {
    return Array.from(this.templates.keys());
  }

  /**
   * Apply template to create video configuration
   */
  applyTemplate(templateName, variables) {
    const template = this.getTemplate(templateName);
    if (!template) {
      throw new Error(`Template not found: ${templateName}`);
    }

    let config = JSON.stringify(template);
    
    // Replace variables
    for (const [key, value] of Object.entries(variables)) {
      config = config.replace(new RegExp(`{{${key}}}`, 'g'), value);
    }

    return JSON.parse(config);
  }

  /**
   * Create a new template
   */
  createTemplate(name, config) {
    const templatePath = path.join(this.templatesDir, `${name}.json`);
    fs.writeFileSync(templatePath, JSON.stringify(config, null, 2));
    this.templates.set(name, config);
    console.log(`✓ Template created: ${name}`);
    return config;
  }
}

export default { LanguageManager, TemplateManager };
