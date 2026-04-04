// HeyGen Pipeline Index
// Main entry point for programmatic usage

export { HeyGenClient, loadConfig } from './heygen-client.js';
export { VideoQueue, BatchProcessor } from './queue.js';
export { VideoStorage } from './storage.js';
export { LanguageManager, TemplateManager } from './language.js';

// Re-export as default object
import { HeyGenClient, loadConfig } from './heygen-client.js';
import { VideoQueue, BatchProcessor } from './queue.js';
import { VideoStorage } from './storage.js';
import { LanguageManager, TemplateManager } from './language.js';

export default {
  HeyGenClient,
  loadConfig,
  VideoQueue,
  BatchProcessor,
  VideoStorage,
  LanguageManager,
  TemplateManager
};
