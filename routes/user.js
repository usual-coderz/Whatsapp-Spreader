const express = require('express');
const router = express.Router();
const multer = require('multer');
const path = require('path');
const fs = require('fs');
const User = require('../models/User');
const Broadcast = require('../models/Broadcast');
const NumberList = require('../models/NumberList');
const Admin = require('../models/Admin');
const WhatsAppManager = require('../utils/whatsapp');
const { authenticateUser } = require('../middleware/auth');

// Configure multer for file upload
const uploadDir = path.join(__dirname, '..', 'uploads');
if (!fs.existsSync(uploadDir)) fs.mkdirSync(uploadDir, { recursive: true });

const storage = multer.diskStorage({
    destination: (req, file, cb) => cb(null, uploadDir),
    filename: (req, file, cb) => cb(null, `numbers_${req.userId}_${Date.now()}.txt`)
});
const upload = multer({ storage });

// Get user profile
router.get('/profile', authenticateUser, async (req, res) => {
    try {
        const user = await User.findById(req.userId).select('-password');
        if (!user) {
            return res.status(404).json({ success: false, message: 'User not found' });
        }
        res.json({ success: true, user });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Connect WhatsApp
router.post('/connect-whatsapp', authenticateUser, async (req, res) => {
    try {
        const user = await User.findById(req.userId);
        if (!user) {
            return res.status(404).json({ success: false, message: 'User not found' });
        }

        const io = req.app.get('io');
        const clients = req.app.get('whatsappClients');

        // Disconnect existing if any
        if (clients[req.userId]) {
            try {
                await clients[req.userId].destroy();
            } catch (e) {}
            delete clients[req.userId];
        }

        const manager = new WhatsAppManager(io, clients);
        const result = await manager.connectUser(req.userId, user.username);

        if (result.success) {
            res.json({ success: true, message: 'WhatsApp connecting... Scan QR code' });
        } else {
            res.status(500).json({ success: false, message: result.error });
        }
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Disconnect WhatsApp
router.post('/disconnect-whatsapp', authenticateUser, async (req, res) => {
    try {
        const io = req.app.get('io');
        const clients = req.app.get('whatsappClients');
        const manager = new WhatsAppManager(io, clients);
        await manager.disconnectUser(req.userId);
        res.json({ success: true, message: 'WhatsApp disconnected' });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get QR code status
router.get('/qr-status', authenticateUser, async (req, res) => {
    try {
        const user = await User.findById(req.userId);
        res.json({
            success: true,
            connected: user.whatsappConnected,
            number: user.whatsappNumber,
            isBroadcasting: user.isBroadcasting
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Upload numbers file
router.post('/upload-numbers', authenticateUser, upload.single('numbers'), async (req, res) => {
    try {
        if (!req.file) {
            return res.status(400).json({ success: false, message: 'No file uploaded' });
        }

        const content = fs.readFileSync(req.file.path, 'utf8');
        const numbers = content.split('\n')
            .map(n => n.trim())
            .filter(n => n.length > 0 && /^[0-9+\-\s()]+$/.test(n));

        if (numbers.length === 0) {
            fs.unlinkSync(req.file.path);
            return res.status(400).json({ success: false, message: 'No valid numbers found' });
        }

        // Save to database
        let numberList = await NumberList.findOne({ userId: req.userId });
        if (numberList) {
            numberList.numbers = numbers;
            numberList.filename = req.file.originalname;
        } else {
            numberList = new NumberList({
                userId: req.userId,
                filename: req.file.originalname,
                numbers
            });
        }
        await numberList.save();

        // Clean up file
        fs.unlinkSync(req.file.path);

        res.json({
            success: true,
            count: numbers.length,
            message: `${numbers.length} numbers uploaded successfully`
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get uploaded numbers
router.get('/numbers', authenticateUser, async (req, res) => {
    try {
        const numberList = await NumberList.findOne({ userId: req.userId });
        if (!numberList) {
            return res.json({ success: true, numbers: [], count: 0 });
        }
        res.json({
            success: true,
            numbers: numberList.numbers,
            count: numberList.numbers.length,
            filename: numberList.filename
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Start broadcast
router.post('/start-broadcast', authenticateUser, async (req, res) => {
    try {
        const user = await User.findById(req.userId);
        if (!user) {
            return res.status(404).json({ success: false, message: 'User not found' });
        }

        if (!user.whatsappConnected) {
            return res.status(400).json({ success: false, message: 'WhatsApp not connected' });
        }

        if (user.isBroadcasting) {
            return res.status(400).json({ success: false, message: 'Broadcast already running' });
        }

        // Get target message from admin
        const admin = await Admin.findOne();
        if (!admin || !admin.targetMessage) {
            return res.status(400).json({ success: false, message: 'No target message set by admin' });
        }

        // Get numbers
        const numberList = await NumberList.findOne({ userId: req.userId });
        if (!numberList || numberList.numbers.length === 0) {
            return res.status(400).json({ success: false, message: 'No numbers uploaded. Please upload numbers.txt first' });
        }

        // Create broadcast record
        const broadcast = new Broadcast({
            userId: req.userId,
            message: admin.targetMessage,
            totalNumbers: numberList.numbers.length,
            status: 'pending'
        });
        await broadcast.save();

        // Mark user as broadcasting
        user.isBroadcasting = true;
        await user.save();

        // Start broadcast in background
        const io = req.app.get('io');
        const clients = req.app.get('whatsappClients');
        const manager = new WhatsAppManager(io, clients);

        // Don't await - let it run in background
        manager.startBroadcast(req.userId, admin.targetMessage, numberList.numbers, broadcast._id);

        res.json({
            success: true,
            broadcastId: broadcast._id,
            message: `Broadcast started to ${numberList.numbers.length} numbers`
        });
    } catch (error) {
        // Reset broadcasting status on error
        await User.findByIdAndUpdate(req.userId, { isBroadcasting: false });
        res.status(500).json({ success: false, message: error.message });
    }
});

// Stop broadcast
router.post('/stop-broadcast', authenticateUser, async (req, res) => {
    try {
        const broadcast = await Broadcast.findOne({
            userId: req.userId,
            status: { $in: ['pending', 'running'] }
        }).sort({ createdAt: -1 });

        if (broadcast) {
            broadcast.status = 'stopped';
            broadcast.completedAt = new Date();
            await broadcast.save();
        }

        await User.findByIdAndUpdate(req.userId, { isBroadcasting: false });

        res.json({ success: true, message: 'Broadcast stopped' });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get broadcast status
router.get('/broadcast-status', authenticateUser, async (req, res) => {
    try {
        const broadcast = await Broadcast.findOne({ userId: req.userId })
            .sort({ createdAt: -1 });

        if (!broadcast) {
            return res.json({ success: true, broadcast: null });
        }

        res.json({ success: true, broadcast });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get broadcast history
router.get('/broadcast-history', authenticateUser, async (req, res) => {
    try {
        const broadcasts = await Broadcast.find({ userId: req.userId })
            .sort({ createdAt: -1 })
            .limit(50);
        res.json({ success: true, broadcasts });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get target message
router.get('/target-message', authenticateUser, async (req, res) => {
    try {
        const admin = await Admin.findOne();
        res.json({ success: true, message: admin?.targetMessage || '' });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

module.exports = router;