/**
 * Video Storage and Retrieval System
 * Manages local video files and metadata
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export class VideoStorage {
  constructor(options = {}) {
    this.videoDir = options.videoDir || './videos';
    this.metadataDir = options.metadataDir || './metadata';
    this.ensureDirectories();
  }

  ensureDirectories() {
    [this.videoDir, this.metadataDir].forEach(dir => {
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    });
  }

  /**
   * Save video metadata
   */
  saveMetadata(videoId, metadata) {
    const metadataPath = path.join(this.metadataDir, `${videoId}.json`);
    const data = {
      videoId,
      created: new Date().toISOString(),
      ...metadata
    };
    fs.writeFileSync(metadataPath, JSON.stringify(data, null, 2));
    return data;
  }

  /**
   * Get video metadata
   */
  getMetadata(videoId) {
    const metadataPath = path.join(this.metadataDir, `${videoId}.json`);
    if (!fs.existsSync(metadataPath)) return null;
    return JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
  }

  /**
   * List all videos with metadata
   */
  listVideos(filter = {}) {
    const { client, language, status, limit } = filter;
    
    if (!fs.existsSync(this.metadataDir)) return [];
    
    const files = fs.readdirSync(this.metadataDir)
      .filter(f => f.endsWith('.json'))
      .map(f => {
        const data = JSON.parse(fs.readFileSync(path.join(this.metadataDir, f), 'utf8'));
        return data;
      });

    let results = files;

    if (client) {
      results = results.filter(v => v.client === client);
    }

    if (language) {
      results = results.filter(v => v.language === language);
    }

    if (status) {
      results = results.filter(v => v.status === status);
    }

    // Sort by creation date, newest first
    results.sort((a, b) => new Date(b.created) - new Date(a.created));

    if (limit) {
      results = results.slice(0, limit);
    }

    return results;
  }

  /**
   * Download video from URL and save locally
   */
  async downloadVideo(videoId, videoUrl, options = {}) {
    const videoPath = path.join(this.videoDir, `${videoId}.mp4`);
    
    console.log(`⬇️  Downloading video: ${videoId}`);
    
    try {
      const response = await fetch(videoUrl);
      
      if (!response.ok) {
        throw new Error(`Failed to download: ${response.status}`);
      }

      const buffer = Buffer.from(await response.arrayBuffer());
      fs.writeFileSync(videoPath, buffer);

      // Update metadata
      const metadata = this.getMetadata(videoId) || {};
      metadata.localPath = videoPath;
      metadata.downloaded = new Date().toISOString();
      metadata.fileSize = buffer.length;
      this.saveMetadata(videoId, metadata);

      console.log(`✅ Video saved: ${videoPath}`);
      console.log(`   Size: ${(buffer.length / 1024 / 1024).toFixed(2)} MB`);
      
      return videoPath;
    } catch (error) {
      console.error(`❌ Download failed: ${error.message}`);
      throw error;
    }
  }

  /**
   * Update video status and URL when ready
   */
  updateVideoStatus(videoId, status, videoUrl = null) {
    const metadata = this.getMetadata(videoId) || {};
    metadata.status = status;
    metadata.updated = new Date().toISOString();
    
    if (videoUrl) {
      metadata.videoUrl = videoUrl;
    }
    
    this.saveMetadata(videoId, metadata);
    return metadata;
  }

  /**
   * Get local video path
   */
  getVideoPath(videoId) {
    return path.join(this.videoDir, `${videoId}.mp4`);
  }

  /**
   * Check if video exists locally
   */
  hasLocalVideo(videoId) {
    return fs.existsSync(this.getVideoPath(videoId));
  }

  /**
   * Delete video and metadata
   */
  deleteVideo(videoId) {
    const videoPath = this.getVideoPath(videoId);
    const metadataPath = path.join(this.metadataDir, `${videoId}.json`);

    if (fs.existsSync(videoPath)) {
      fs.unlinkSync(videoPath);
    }

    if (fs.existsSync(metadataPath)) {
      fs.unlinkSync(metadataPath);
    }

    console.log(`🗑️  Deleted video: ${videoId}`);
    return true;
  }

  /**
   * Clean up old videos
   */
  cleanup(maxAgeDays = 30, maxCount = null) {
    const videos = this.listVideos();
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - maxAgeDays);

    let deleted = 0;

    // Delete old videos
    videos.forEach(video => {
      const created = new Date(video.created);
      if (created < cutoff) {
        this.deleteVideo(video.videoId);
        deleted++;
      }
    });

    // If still over max count, delete oldest
    if (maxCount) {
      const remaining = this.listVideos();
      if (remaining.length > maxCount) {
        const toDelete = remaining.slice(maxCount);
        toDelete.forEach(video => {
          this.deleteVideo(video.videoId);
          deleted++;
        });
      }
    }

    console.log(`🧹 Cleanup complete: ${deleted} videos removed`);
    return deleted;
  }

  /**
   * Generate download report
   */
  generateReport() {
    const videos = this.listVideos();
    
    const report = {
      total: videos.length,
      byStatus: {},
      byClient: {},
      byLanguage: {},
      totalSize: 0,
      storageUsed: 0
    };

    videos.forEach(video => {
      // Count by status
      report.byStatus[video.status] = (report.byStatus[video.status] || 0) + 1;
      
      // Count by client
      if (video.client) {
        report.byClient[video.client] = (report.byClient[video.client] || 0) + 1;
      }
      
      // Count by language
      if (video.language) {
        report.byLanguage[video.language] = (report.byLanguage[video.language] || 0) + 1;
      }

      // Calculate storage
      if (video.fileSize) {
        report.totalSize += video.fileSize;
      }
      
      if (this.hasLocalVideo(video.videoId)) {
        const stats = fs.statSync(this.getVideoPath(video.videoId));
        report.storageUsed += stats.size;
      }
    });

    return report;
  }
}

export default VideoStorage;
