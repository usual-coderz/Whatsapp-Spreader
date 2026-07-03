const { Client, RemoteAuth } = require('whatsapp-web.js');
const { MongoStore } = require('wwebjs-mongo');
const mongoose = require('mongoose');
const qrcode = require('qrcode');
const User = require('../models/User');

class WhatsAppManager {
    constructor(io, clientsMap) {
        this.io = io;
        this.clients = clientsMap;
    }

    async connectUser(userId, username) {
        try {
            const store = new MongoStore({ mongoose: mongoose });
            const clientId = `user_${userId}`;

            const client = new Client({
                authStrategy: new RemoteAuth({
                    store: store,
                    clientId: clientId,
                    backupSyncIntervalMs: 300000
                }),
                puppeteer: {
                    headless: true,
                    args: [
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--single-process'
                    ]
                }
            });

            client.on('qr', async (qr) => {
                try {
                    const qrImage = await qrcode.toDataURL(qr);
                    this.io.to(`qr_${userId}`).emit('qr-code', { qr: qrImage, userId });
                    console.log(`📱 QR generated for user ${username}`);
                } catch (err) {
                    console.error('QR generation error:', err);
                }
            });

            client.on('ready', async () => {
                console.log(`✅ WhatsApp connected for user ${username}`);
                
                const user = await User.findById(userId);
                if (user) {
                    user.whatsappConnected = true;
                    user.whatsappNumber = client.info?.wid?.user || '';
                    await user.save();
                }

                this.io.to(`qr_${userId}`).emit('whatsapp-connected', {
                    userId,
                    number: client.info?.wid?.user || 'Connected'
                });
            });

            client.on('disconnected', async (reason) => {
                console.log(`❌ WhatsApp disconnected for user ${username}:`, reason);
                const user = await User.findById(userId);
                if (user) {
                    user.whatsappConnected = false;
                    await user.save();
                }
                delete this.clients[userId];
                this.io.to(`qr_${userId}`).emit('whatsapp-disconnected', { userId, reason });
            });

            client.on('auth_failure', (msg) => {
                console.error(`❌ Auth failure for user ${username}:`, msg);
                this.io.to(`qr_${userId}`).emit('auth-failure', { userId, message: msg });
            });

            await client.initialize();
            this.clients[userId] = client;
            
            return { success: true, client };
        } catch (error) {
            console.error(`Error connecting WhatsApp for user ${username}:`, error);
            return { success: false, error: error.message };
        }
    }

    async disconnectUser(userId) {
        try {
            const client = this.clients[userId];
            if (client) {
                await client.destroy();
                delete this.clients[userId];
            }
            
            await User.findByIdAndUpdate(userId, {
                whatsappConnected: false,
                isBroadcasting: false
            });

            return { success: true };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    async sendMessage(client, number, message) {
        try {
            // Format number: remove any non-digit characters, ensure it has country code
            let cleanNumber = number.replace(/[^0-9]/g, '');
            if (cleanNumber.length === 10) {
                cleanNumber = '91' + cleanNumber; // Default India code, adjust as needed
            }
            
            const chatId = `${cleanNumber}@c.us`;
            await client.sendMessage(chatId, message);
            return { success: true };
        } catch (error) {
            return { success: false, error: error.message };
        }
    }

    async startBroadcast(userId, message, numbers, broadcastId) {
        const client = this.clients[userId];
        if (!client) {
            return { success: false, error: 'WhatsApp not connected' };
        }

        const Broadcast = require('../models/Broadcast');
        const broadcast = await Broadcast.findById(broadcastId);
        if (!broadcast) return { success: false, error: 'Broadcast not found' };

        broadcast.status = 'running';
        broadcast.startedAt = new Date();
        await broadcast.save();

        let successCount = 0;
        let failCount = 0;
        const total = numbers.length;

        for (let i = 0; i < numbers.length; i++) {
            // Check if broadcast was stopped
            const currentBroadcast = await Broadcast.findById(broadcastId);
            if (currentBroadcast?.status === 'stopped') break;

            const number = numbers[i].trim();
            if (!number) continue;

            const result = await this.sendMessage(client, number, message);
            
            if (result.success) {
                successCount++;
            } else {
                failCount++;
            }

            // Update progress
            broadcast.successCount = successCount;
            broadcast.failCount = failCount;
            await broadcast.save();

            // Emit progress
            this.io.to(`user_${userId}`).emit('broadcast-progress', {
                broadcastId,
                total,
                sent: successCount,
                failed: failCount,
                current: i + 1,
                currentNumber: number
            });

            // Rate limiting delay (1-2 seconds between messages to avoid ban)
            await new Promise(resolve => setTimeout(resolve, 1500 + Math.random() * 1000));
        }

        broadcast.status = 'completed';
        broadcast.completedAt = new Date();
        await broadcast.save();

        // Update user stats
        await User.findByIdAndUpdate(userId, {
            $inc: { totalSent: successCount, totalFailed: failCount },
            isBroadcasting: false
        });

        this.io.to(`user_${userId}`).emit('broadcast-complete', {
            broadcastId,
            total,
            sent: successCount,
            failed: failCount
        });

        return { success: true, sent: successCount, failed: failCount };
    }
}

module.exports = WhatsAppManager;