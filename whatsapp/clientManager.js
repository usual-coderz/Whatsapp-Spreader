const { Client, RemoteAuth } = require('whatsapp-web.js');
const { MongoStore } = require('wwebjs-mongo');
const mongoose = require('mongoose');
const qrcode = require('qrcode');
const fs = require('fs');
const path = require('path');

class WhatsAppClientManager {
  constructor() {
    this.clients = new Map();      // userId -> { client, status, qr }
    this.broadcasting = new Map(); // userId -> boolean
  }

  async createClient(userId, io) {
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
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
      }
    });

    client.on('qr', async (qr) => {
      try {
        const qrDataUrl = await qrcode.toDataURL(qr);
        this.clients.set(userId, { ...this.clients.get(userId), client, status: 'qr_ready', qr: qrDataUrl });
        if (io) {
          io.to(`user_${userId}`).emit('qr_code', { qr: qrDataUrl });
        }
      } catch (err) {
        console.error('QR gen error:', err);
      }
    });

    client.on('ready', () => {
      console.log(`✅ User ${userId} WhatsApp connected`);
      this.clients.set(userId, { ...this.clients.get(userId), client, status: 'connected', qr: null });
      if (io) {
        io.to(`user_${userId}`).emit('whatsapp_status', { status: 'connected' });
      }
    });

    client.on('disconnected', (reason) => {
      console.log(`❌ User ${userId} disconnected:`, reason);
      this.clients.set(userId, { ...this.clients.get(userId), client, status: 'disconnected', qr: null });
      if (io) {
        io.to(`user_${userId}`).emit('whatsapp_status', { status: 'disconnected' });
      }
    });

    client.on('auth_failure', (msg) => {
      console.log(`⚠️ User ${userId} auth failure:`, msg);
      this.clients.set(userId, { ...this.clients.get(userId), client, status: 'disconnected' });
    });

    this.clients.set(userId, { client, status: 'initializing', qr: null });
    client.initialize();
    return client;
  }

  async destroyClient(userId) {
    const existing = this.clients.get(userId);
    if (existing && existing.client) {
      try {
        await existing.client.destroy();
      } catch (e) { /* ignore */ }
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
    return entry ? { status: entry.status, qr: entry.qr } : { status: 'disconnected', qr: null };
  }

  async startBroadcast(userId, message, numbers, io, Broadcast) {
    if (this.broadcasting.get(userId)) {
      return { error: 'Already broadcasting' };
    }

    const client = this.getClient(userId);
    if (!client) {
      return { error: 'WhatsApp not connected' };
    }

    this.broadcasting.set(userId, true);
    const broadcast = new Broadcast({
      userId,
      message,
      totalNumbers: numbers.length,
      status: 'running',
      startedAt: new Date()
    });
    await broadcast.save();

    let success = 0;
    let fail = 0;

    try {
      for (let i = 0; i < numbers.length; i++) {
        if (!this.broadcasting.get(userId)) break; // stop flag

        let number = numbers[i].trim();
        if (!number) continue;

        // Format number
        if (!number.includes('@c.us')) {
          number = `${number.replace(/[^0-9]/g, '')}@c.us`;
        }

        try {
          await client.sendMessage(number, message);
          success++;
        } catch (err) {
          console.error(`Send fail to ${number}:`, err.message);
          fail++;
        }

        // Emit progress
        if (io) {
          io.to(`user_${userId}`).emit('broadcast_progress', {
            total: numbers.length,
            done: success + fail,
            success,
            fail,
            current: i + 1
          });
        }

        // Delay to avoid rate limiting
        await new Promise(r => setTimeout(r, 3000 + Math.random() * 2000));
      }
    } catch (err) {
      console.error('Broadcast error:', err);
    }

    this.broadcasting.set(userId, false);

    broadcast.successCount = success;
    broadcast.failCount = fail;
    broadcast.status = 'completed';
    broadcast.completedAt = new Date();
    await broadcast.save();

    if (io) {
      io.to(`user_${userId}`).emit('broadcast_complete', {
        total: numbers.length,
        success,
        fail
      });
    }

    return { success, fail, total: numbers.length };
  }

  stopBroadcast(userId) {
    this.broadcasting.set(userId, false);
  }
}

module.exports = new WhatsAppClientManager();