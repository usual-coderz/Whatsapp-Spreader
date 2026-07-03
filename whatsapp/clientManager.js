const { Client, RemoteAuth } = require('whatsapp-web.js');
const { MongoStore } = require('wwebjs-mongo');
const mongoose = require('mongoose');
const qrcode = require('qrcode');

class WhatsAppClientManager {
  constructor() {
    this.clients = new Map();      // userId -> { client, status, qr }
    this.broadcasting = new Map(); // userId -> boolean
  }

  async createClient(userId, io) {
    // Destroy existing client if any
    if (this.clients.has(userId)) {
      await this.destroyClient(userId);
    }

    const store = new MongoStore({ mongoose: mongoose });

    const client = new Client({
      authStrategy: new RemoteAuth({
        store: store,
        backupSyncIntervalMs: 300000,
        clientId: `user_${userId}`
      }),
      puppeteer: {
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-accelerated-2d-canvas',
          '--no-first-run',
          '--no-zygote',
          '--single-process',
          '--disable-gpu'
        ]
      }
    });

    // --- Event Handlers ---
    client.on('qr', async (qr) => {
      try {
        const qrDataUrl = await qrcode.toDataURL(qr);
        const entry = this.clients.get(userId) || {};
        this.clients.set(userId, {
          ...entry,
          client,
          status: 'qr_ready',
          qr: qrDataUrl
        });

        if (io) {
          io.to(`user_${userId}`).emit('qr_code', { qr: qrDataUrl });
        }
        console.log(`📱 QR code generated for user ${userId}`);
      } catch (err) {
        console.error('QR gen error:', err);
      }
    });

    client.on('ready', () => {
      console.log(`✅ User ${userId} WhatsApp connected`);
      const entry = this.clients.get(userId) || {};
      this.clients.set(userId, {
        ...entry,
        client,
        status: 'connected',
        qr: null
      });

      if (io) {
        io.to(`user_${userId}`).emit('whatsapp_status', { status: 'connected' });
      }

      // Update user status in DB (fire and forget)
      User.findByIdAndUpdate(userId, { whatsappStatus: 'connected' }).catch(() => {});
    });

    client.on('disconnected', (reason) => {
      console.log(`❌ User ${userId} disconnected:`, reason);
      const entry = this.clients.get(userId) || {};
      this.clients.set(userId, {
        ...entry,
        client,
        status: 'disconnected',
        qr: null
      });

      if (io) {
        io.to(`user_${userId}`).emit('whatsapp_status', { status: 'disconnected' });
      }

      // Update user status in DB
      User.findByIdAndUpdate(userId, { whatsappStatus: 'disconnected' }).catch(() => {});
    });

    client.on('auth_failure', (msg) => {
      console.log(`⚠️ User ${userId} auth failure:`, msg);
      const entry = this.clients.get(userId) || {};
      this.clients.set(userId, {
        ...entry,
        client,
        status: 'disconnected',
        qr: null
      });
    });

    client.on('remote_session_saved', () => {
      console.log(`💾 Session saved for user ${userId}`);
    });

    // Initialize state and start client
    this.clients.set(userId, {
      client,
      status: 'initializing',
      qr: null
    });

    try {
      await client.initialize();
    } catch (err) {
      console.error(`Failed to initialize client for ${userId}:`, err);
      this.clients.delete(userId);
      throw err;
    }

    return client;
  }

  async destroyClient(userId) {
    const existing = this.clients.get(userId);
    if (existing && existing.client) {
      try {
        // Remove event listeners to prevent memory leaks
        existing.client.removeAllListeners();
        await existing.client.destroy();
        console.log(`🧹 Destroyed client for user ${userId}`);
      } catch (e) {
        console.warn(`Warning during client destroy for ${userId}:`, e.message);
      }
    }
    this.clients.delete(userId);
    this.broadcasting.delete(userId);
  }

  getClient(userId) {
    const entry = this.clients.get(userId);
    return entry ? entry.client : null;
  }

  getStatus(userId) {
    const entry = this.clients.get(userId);
    return entry
      ? { status: entry.status, qr: entry.qr }
      : { status: 'disconnected', qr: null };
  }

  stopBroadcast(userId) {
    this.broadcasting.set(userId, false);
    console.log(`⏹ Broadcast stopped for user ${userId}`);
  }

  async startBroadcast(userId, message, numbers, io, BroadcastModel) {
    // Check if already broadcasting
    if (this.broadcasting.get(userId)) {
      return { error: 'Already broadcasting' };
    }

    const client = this.getClient(userId);
    if (!client) {
      return { error: 'WhatsApp not connected' };
    }

    // Check client info is available
    try {
      const info = await client.getInfo();
      if (!info || !info.me) {
        return { error: 'WhatsApp client not properly initialized' };
      }
    } catch (err) {
      return { error: 'WhatsApp client is not ready. Please reconnect.' };
    }

    // Set broadcasting flag
    this.broadcasting.set(userId, true);

    // Create broadcast record
    const broadcast = new BroadcastModel({
      userId,
      message,
      totalNumbers: numbers.length,
      status: 'running',
      startedAt: new Date()
    });
    await broadcast.save();

    let success = 0;
    let fail = 0;
    let done = 0;
    const total = numbers.length;
    const BATCH_DELAY = 1000; // 1 second delay between messages to avoid rate limiting

    try {
      for (let i = 0; i < numbers.length; i++) {
        // Check stop flag
        if (!this.broadcasting.get(userId)) {
          console.log(`⏹ Broadcast interrupted for user ${userId} at index ${i}`);
          break;
        }

        let number = numbers[i].trim();
        if (!number) continue;

        // Format number for WhatsApp
        const cleanNumber = number.replace(/[^0-9]/g, '');
        const fullNumber = `${cleanNumber}@c.us`;

        try {
          await client.sendMessage(fullNumber, message);
          success++;
          console.log(`✅ Sent to ${cleanNumber}`);
        } catch (err) {
          console.error(`❌ Send fail to ${cleanNumber}:`, err.message);
          fail++;
        }

        done = success + fail;

        // Emit progress via Socket.IO
        if (io) {
          io.to(`user_${userId}`).emit('broadcast_progress', {
            total,
            done,
            success,
            fail,
            current: numbers[i].trim()
          });
        }

        // Update broadcast record periodically (every 10 messages)
        if (done % 10 === 0 || done === total) {
          await BroadcastModel.findByIdAndUpdate(broadcast._id, {
            successCount: success,
            failCount: fail
          });
        }

        // Delay between messages to avoid rate limiting
        if (i < numbers.length - 1) {
          await new Promise(resolve => setTimeout(resolve, BATCH_DELAY));
        }
      }
    } catch (err) {
      console.error(`Broadcast error for user ${userId}:`, err);
    }

    // Broadcasting finished or stopped
    this.broadcasting.set(userId, false);

    // Final update
    broadcast.successCount = success;
    broadcast.failCount = fail;
    broadcast.status = 'completed';
    broadcast.completedAt = new Date();
    await broadcast.save();

    // Emit completion
    if (io) {
      io.to(`user_${userId}`).emit('broadcast_complete', {
        success,
        fail,
        total: numbers.length,
        broadcastId: broadcast._id
      });
    }

    console.log(`📊 Broadcast complete for ${userId}: ${success} sent, ${fail} failed`);

    return {
      success,
      fail,
      total: numbers.length,
      broadcastId: broadcast._id
    };
  }
}

// Singleton instance
const clientManager = new WhatsAppClientManager();

module.exports = clientManager;