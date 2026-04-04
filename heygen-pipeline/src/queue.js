/**
 * Video Queue Management
 * Handles batch processing and workflow management
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class VideoQueue {
  constructor(storageDir = './metadata') {
    this.storageDir = storageDir;
    this.queueFile = path.join(storageDir, 'queue.json');
    this.ensureStorage();
  }

  ensureStorage() {
    if (!fs.existsSync(this.storageDir)) {
      fs.mkdirSync(this.storageDir, { recursive: true });
    }
    if (!fs.existsSync(this.queueFile)) {
      fs.writeFileSync(this.queueFile, JSON.stringify({ jobs: [], completed: [] }, null, 2));
    }
  }

  loadQueue() {
    return JSON.parse(fs.readFileSync(this.queueFile, 'utf8'));
  }

  saveQueue(queue) {
    fs.writeFileSync(this.queueFile, JSON.stringify(queue, null, 2));
  }

  /**
   * Add a job to the queue
   */
  addJob(job) {
    const queue = this.loadQueue();
    const jobId = `job_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    
    const jobEntry = {
      id: jobId,
      status: 'pending',
      created: new Date().toISOString(),
      attempts: 0,
      maxRetries: job.maxRetries || 3,
      ...job
    };
    
    queue.jobs.push(jobEntry);
    this.saveQueue(queue);
    
    console.log(`✓ Job added to queue: ${jobId}`);
    return jobId;
  }

  /**
   * Get next pending job
   */
  getNextJob() {
    const queue = this.loadQueue();
    return queue.jobs.find(j => j.status === 'pending' && j.attempts < j.maxRetries);
  }

  /**
   * Update job status
   */
  updateJob(jobId, updates) {
    const queue = this.loadQueue();
    const jobIndex = queue.jobs.findIndex(j => j.id === jobId);
    
    if (jobIndex === -1) return null;
    
    queue.jobs[jobIndex] = { ...queue.jobs[jobIndex], ...updates };
    
    if (updates.status === 'completed' || updates.status === 'failed') {
      const job = queue.jobs.splice(jobIndex, 1)[0];
      queue.completed.push(job);
    }
    
    this.saveQueue(queue);
    return queue.jobs[jobIndex] || queue.completed[queue.completed.length - 1];
  }

  /**
   * Get job by ID
   */
  getJob(jobId) {
    const queue = this.loadQueue();
    const job = queue.jobs.find(j => j.id === jobId);
    if (job) return job;
    return queue.completed.find(j => j.id === jobId);
  }

  /**
   * List all jobs
   */
  listJobs(filter = 'all') {
    const queue = this.loadQueue();
    
    switch (filter) {
      case 'pending':
        return queue.jobs.filter(j => j.status === 'pending');
      case 'processing':
        return queue.jobs.filter(j => j.status === 'processing');
      case 'completed':
        return queue.completed.filter(j => j.status === 'completed');
      case 'failed':
        return [...queue.jobs, ...queue.completed].filter(j => j.status === 'failed');
      default:
        return [...queue.jobs, ...queue.completed];
    }
  }

  /**
   * Clear completed jobs older than specified days
   */
  clearOldJobs(days = 30) {
    const queue = this.loadQueue();
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    
    const beforeCount = queue.completed.length;
    queue.completed = queue.completed.filter(j => {
      const completedDate = new Date(j.completed || j.updated || j.created);
      return completedDate > cutoff;
    });
    
    this.saveQueue(queue);
    console.log(`Cleared ${beforeCount - queue.completed.length} old jobs`);
    return beforeCount - queue.completed.length;
  }
}

/**
 * Batch Processor
 */
export class BatchProcessor {
  constructor(heygenClient, queue, options = {}) {
    this.client = heygenClient;
    this.queue = queue;
    this.concurrency = options.concurrency || 2;
    this.retryDelay = options.retryDelay || 30000;
    this.running = false;
    this.activeJobs = new Set();
  }

  /**
   * Start processing the queue
   */
  async start() {
    if (this.running) return;
    this.running = true;
    
    console.log('🚀 Starting batch processor...');
    console.log(`   Concurrency: ${this.concurrency}`);
    
    while (this.running) {
      // Fill up to concurrency limit
      while (this.activeJobs.size < this.concurrency) {
        const job = this.queue.getNextJob();
        if (!job) break;
        
        this.processJob(job);
      }
      
      // Wait a bit before checking again
      await this.sleep(5000);
    }
  }

  /**
   * Stop processing
   */
  stop() {
    console.log('⏹️  Stopping batch processor...');
    this.running = false;
  }

  /**
   * Process a single job
   */
  async processJob(job) {
    this.activeJobs.add(job.id);
    
    console.log(`▶️  Processing job: ${job.id}`);
    this.queue.updateJob(job.id, { 
      status: 'processing', 
      started: new Date().toISOString(),
      attempts: job.attempts + 1
    });

    try {
      let result;
      
      if (job.type === 'template') {
        result = await this.client.createFromTemplate(job.templateId, job.variables);
      } else {
        result = await this.client.createVideo({
          script: job.script,
          avatarId: job.avatarId,
          voiceId: job.voiceId,
          width: job.width || 1920,
          height: job.height || 1080,
          background: job.background,
          caption: job.caption || false
        });
      }

      this.queue.updateJob(job.id, {
        status: 'completed',
        videoId: result.data?.video_id,
        completed: new Date().toISOString(),
        result: result
      });

      console.log(`✅ Job completed: ${job.id}`);
      console.log(`   Video ID: ${result.data?.video_id}`);

    } catch (error) {
      console.error(`❌ Job failed: ${job.id}`);
      console.error(`   Error: ${error.message}`);

      const shouldRetry = job.attempts < job.maxRetries;
      
      this.queue.updateJob(job.id, {
        status: shouldRetry ? 'pending' : 'failed',
        error: error.message,
        updated: new Date().toISOString()
      });

      if (shouldRetry) {
        console.log(`   Will retry (${job.attempts + 1}/${job.maxRetries})`);
        await this.sleep(this.retryDelay);
      }
    } finally {
      this.activeJobs.delete(job.id);
    }
  }

  sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}

export default { VideoQueue, BatchProcessor };
