/**
 * CrowdTamers HeyGen CLI
 * Command-line interface for video generation pipeline
 */

import { Command } from 'commander';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { HeyGenClient, loadConfig } from './heygen-client.js';
import { VideoQueue, BatchProcessor } from './queue.js';
import { VideoStorage } from './storage.js';
import { LanguageManager, TemplateManager } from './language.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const program = new Command();

// Default config path
const DEFAULT_CONFIG = path.join(process.cwd(), 'config.yaml');

/**
 * Initialize all components
 */
function initialize(configPath) {
  const config = loadConfig(configPath);
  
  // Load API key
  const apiKeyPath = config.heygen?.api_key_path?.replace('~', process.env.HOME) || 
                     path.join(process.env.HOME, '.config/heygen/api_key');
  const apiKey = fs.readFileSync(apiKeyPath, 'utf8').trim();
  
  const client = new HeyGenClient(apiKey);
  const storage = new VideoStorage({
    videoDir: config.storage?.video_dir || './videos',
    metadataDir: config.storage?.metadata_dir || './metadata'
  });
  const queue = new VideoQueue(config.storage?.metadata_dir || './metadata');
  const languageManager = new LanguageManager(config);
  const templateManager = new TemplateManager();

  return { client, storage, queue, languageManager, templateManager, config };
}

program
  .name('heygen-pipeline')
  .description('CrowdTamers HeyGen Video Pipeline CLI')
  .version('1.0.0')
  .option('-c, --config <path>', 'config file path', DEFAULT_CONFIG);

// Create video command
program
  .command('create')
  .description('Create a new video from script')
  .requiredOption('-s, --script <text>', 'script text or path to script file')
  .option('-a, --avatar <id>', 'avatar ID')
  .option('-v, --voice <id>', 'voice ID')
  .option('-l, --language <code>', 'language code (en, es, fr, etc.)', 'en')
  .option('--width <pixels>', 'video width', '1920')
  .option('--height <pixels>', 'video height', '1080')
  .option('--client <name>', 'client name for organization')
  .option('--campaign <name>', 'campaign name')
  .option('--caption', 'add captions to video', false)
  .option('--wait', 'wait for video to complete', false)
  .action(async (options) => {
    try {
      const { client, storage, queue, languageManager, config } = initialize(program.opts().config);
      
      // Load script from file if path provided
      let script = options.script;
      if (fs.existsSync(script)) {
        script = fs.readFileSync(script, 'utf8');
      }

      // Get avatar and voice from config or options
      const clientConfig = config.clients?.[options.client] || {};
      const avatarId = options.avatar || clientConfig.default_avatar || config.defaults?.avatar_id;
      const voiceId = options.voice || 
                      languageManager.getVoiceForLanguage(options.language) || 
                      clientConfig.default_voice || 
                      config.defaults?.voice_id;

      if (!avatarId) {
        console.error('❌ Error: Avatar ID is required. Use --avatar or set in config.yaml');
        process.exit(1);
      }

      console.log('🎬 Creating video...');
      console.log(`   Avatar: ${avatarId}`);
      console.log(`   Voice: ${voiceId}`);
      console.log(`   Language: ${options.language}`);
      
      // Add to queue for processing
      const jobId = queue.addJob({
        type: 'video',
        script,
        avatarId,
        voiceId,
        width: parseInt(options.width),
        height: parseInt(options.height),
        caption: options.caption,
        language: options.language,
        client: options.client,
        campaign: options.campaign,
        status: 'pending'
      });

      // Process immediately if --wait flag
      if (options.wait) {
        console.log('\n⏳ Processing video...');
        const processor = new BatchProcessor(client, queue, { concurrency: 1 });
        await processor.processJob(queue.getJob(jobId));
        
        const job = queue.getJob(jobId);
        if (job.status === 'completed') {
          console.log('\n✅ Video created successfully!');
          console.log(`   Video ID: ${job.videoId}`);
          
          // Poll for status
          await pollAndDownload(client, storage, job.videoId);
        }
      } else {
        console.log(`\n✓ Video job queued: ${jobId}`);
        console.log('   Use "heygen-pipeline status" to check progress');
      }

    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
      process.exit(1);
    }
  });

// Batch create command
program
  .command('batch')
  .description('Create multiple videos from a batch file')
  .requiredOption('-f, --file <path>', 'path to batch JSON file')
  .option('--concurrency <n>', 'number of concurrent jobs', '2')
  .action(async (options) => {
    try {
      const { client, storage, queue, languageManager, config } = initialize(program.opts().config);
      
      const batchData = JSON.parse(fs.readFileSync(options.file, 'utf8'));
      
      console.log(`📦 Processing batch: ${batchData.name || 'unnamed'}`);
      console.log(`   Videos: ${batchData.videos?.length || 0}`);
      
      // Add all jobs to queue
      const jobIds = [];
      for (const video of batchData.videos || []) {
        const jobId = queue.addJob({
          type: 'video',
          script: video.script,
          avatarId: video.avatar || batchData.defaultAvatar,
          voiceId: video.voice || batchData.defaultVoice,
          width: video.width || 1920,
          height: video.height || 1080,
          caption: video.caption || false,
          language: video.language || 'en',
          client: batchData.client,
          campaign: batchData.campaign,
          maxRetries: video.maxRetries || 3
        });
        jobIds.push(jobId);
      }

      console.log(`\n✓ ${jobIds.length} jobs added to queue`);
      
      // Start batch processor
      const processor = new BatchProcessor(client, queue, {
        concurrency: parseInt(options.concurrency)
      });
      
      await processor.start();
      
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
      process.exit(1);
    }
  });

// List videos command
program
  .command('list')
  .description('List all videos')
  .option('--client <name>', 'filter by client')
  .option('--language <code>', 'filter by language')
  .option('--status <status>', 'filter by status')
  .option('-l, --limit <n>', 'limit results', '20')
  .action((options) => {
    try {
      const { storage } = initialize(program.opts().config);
      
      const videos = storage.listVideos({
        client: options.client,
        language: options.language,
        status: options.status,
        limit: parseInt(options.limit)
      });

      if (videos.length === 0) {
        console.log('No videos found.');
        return;
      }

      console.log('\n📹 Videos:\n');
      console.log('ID'.padEnd(20) + 'CLIENT'.padEnd(15) + 'LANG'.padEnd(6) + 'STATUS'.padEnd(12) + 'CREATED');
      console.log('-'.repeat(75));
      
      videos.forEach(v => {
        const id = (v.videoId || v.id || 'N/A').slice(0, 18).padEnd(20);
        const client = (v.client || '-').slice(0, 13).padEnd(15);
        const lang = (v.language || '-').padEnd(6);
        const status = (v.status || 'unknown').padEnd(12);
        const created = v.created ? new Date(v.created).toLocaleDateString() : '-';
        
        console.log(`${id}${client}${lang}${status}${created}`);
      });
      
      console.log(`\nTotal: ${videos.length} videos`);

    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Check video status command
program
  .command('status')
  .description('Check video status')
  .argument('[videoId]', 'video ID to check')
  .option('--job <id>', 'check by job ID')
  .action(async (videoId, options) => {
    try {
      const { client, storage, queue } = initialize(program.opts().config);
      
      if (options.job) {
        const job = queue.getJob(options.job);
        if (!job) {
          console.log('Job not found.');
          return;
        }
        console.log('\n📋 Job Status:');
        console.log(JSON.stringify(job, null, 2));
        return;
      }

      if (!videoId) {
        // Show all pending/processing jobs
        const pending = queue.listJobs('pending');
        const processing = queue.listJobs('processing');
        
        console.log('\n🔄 Queue Status:\n');
        console.log(`Pending: ${pending.length}`);
        console.log(`Processing: ${processing.length}`);
        
        if (processing.length > 0) {
          console.log('\nProcessing jobs:');
          processing.forEach(j => {
            console.log(`  - ${j.id}: ${j.script.slice(0, 50)}...`);
          });
        }
        return;
      }

      // Check specific video
      const metadata = storage.getMetadata(videoId);
      if (metadata) {
        console.log('\n📹 Local Metadata:');
        console.log(JSON.stringify(metadata, null, 2));
      }

      // Also check HeyGen API
      console.log('\n🌐 HeyGen Status:');
      const status = await client.getVideoStatus(videoId);
      console.log(JSON.stringify(status, null, 2));

    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Download video command
program
  .command('download')
  .description('Download a completed video')
  .argument('<videoId>', 'video ID to download')
  .action(async (videoId) => {
    try {
      const { client, storage } = initialize(program.opts().config);
      await pollAndDownload(client, storage, videoId);
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Avatars command
program
  .command('avatars')
  .description('List available avatars')
  .action(async () => {
    try {
      const { client } = initialize(program.opts().config);
      const avatars = await client.getAvatars();
      
      console.log('\n🎭 Available Avatars:\n');
      
      if (avatars.data?.avatars) {
        avatars.data.avatars.forEach(avatar => {
          console.log(`ID: ${avatar.avatar_id}`);
          console.log(`Name: ${avatar.avatar_name}`);
          console.log(`Gender: ${avatar.gender}`);
          console.log('---');
        });
      } else {
        console.log(JSON.stringify(avatars, null, 2));
      }
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Voices command
program
  .command('voices')
  .description('List available voices')
  .action(async () => {
    try {
      const { client } = initialize(program.opts().config);
      const voices = await client.getVoices();
      
      console.log('\n🎤 Available Voices:\n');
      
      if (voices.data?.voices) {
        voices.data.voices.forEach(voice => {
          console.log(`ID: ${voice.voice_id}`);
          console.log(`Name: ${voice.display_name}`);
          console.log(`Language: ${voice.language} (${voice.local_name})`);
          console.log(`Type: ${voice.voice_type}`);
          console.log('---');
        });
      } else {
        console.log(JSON.stringify(voices, null, 2));
      }
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Report command
program
  .command('report')
  .description('Generate storage report')
  .action(() => {
    try {
      const { storage } = initialize(program.opts().config);
      const report = storage.generateReport();
      
      console.log('\n📊 Video Storage Report:\n');
      console.log(`Total Videos: ${report.total}`);
      console.log(`Total Size: ${(report.totalSize / 1024 / 1024).toFixed(2)} MB`);
      console.log(`Storage Used: ${(report.storageUsed / 1024 / 1024).toFixed(2)} MB`);
      
      if (Object.keys(report.byStatus).length > 0) {
        console.log('\nBy Status:');
        for (const [status, count] of Object.entries(report.byStatus)) {
          console.log(`  ${status}: ${count}`);
        }
      }
      
      if (Object.keys(report.byClient).length > 0) {
        console.log('\nBy Client:');
        for (const [client, count] of Object.entries(report.byClient)) {
          console.log(`  ${client}: ${count}`);
        }
      }
      
      if (Object.keys(report.byLanguage).length > 0) {
        console.log('\nBy Language:');
        for (const [lang, count] of Object.entries(report.byLanguage)) {
          console.log(`  ${lang}: ${count}`);
        }
      }
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Cleanup command
program
  .command('cleanup')
  .description('Clean up old videos')
  .option('--days <n>', 'delete videos older than N days', '30')
  .option('--max <n>', 'keep only N most recent videos')
  .action((options) => {
    try {
      const { storage } = initialize(program.opts().config);
      const deleted = storage.cleanup(
        parseInt(options.days),
        options.max ? parseInt(options.max) : null
      );
      console.log(`\n🧹 Cleanup complete: ${deleted} videos removed`);
    } catch (error) {
      console.error(`❌ Error: ${error.message}`);
    }
  });

// Helper function to poll for video status and download
async function pollAndDownload(client, storage, videoId, maxAttempts = 60) {
  console.log(`\n⏳ Waiting for video ${videoId}...`);
  
  for (let i = 0; i < maxAttempts; i++) {
    const status = await client.getVideoStatus(videoId);
    const videoData = status.data;
    
    if (videoData?.status === 'completed') {
      console.log('✅ Video ready!');
      console.log(`   URL: ${videoData.video_url}`);
      
      // Download the video
      await storage.downloadVideo(videoId, videoData.video_url);
      storage.updateVideoStatus(videoId, 'downloaded', videoData.video_url);
      
      return videoData;
    }
    
    if (videoData?.status === 'failed') {
      throw new Error(`Video generation failed: ${videoData.error || 'Unknown error'}`);
    }
    
    process.stdout.write(`   Status: ${videoData?.status || 'processing'} (${i + 1}/${maxAttempts})\r`);
    await new Promise(r => setTimeout(r, 10000)); // Wait 10 seconds
  }
  
  throw new Error('Timeout waiting for video');
}

program.parse();
