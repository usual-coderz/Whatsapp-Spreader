const express = require('express');
const router = express.Router();
const bcrypt = require('bcryptjs');
const { v4: uuidv4 } = require('uuid');
const Admin = require('../models/Admin');
const User = require('../models/User');
const Broadcast = require('../models/Broadcast');
const { authenticateAdmin } = require('../middleware/auth');

// Get dashboard stats
router.get('/dashboard', authenticateAdmin, async (req, res) => {
    try {
        const totalUsers = await User.countDocuments();
        const activeUsers = await User.countDocuments({ isActive: true });
        const connectedUsers = await User.countDocuments({ whatsappConnected: true });
        const totalBroadcasts = await Broadcast.countDocuments();
        const admin = await Admin.findById(req.adminId);

        res.json({
            success: true,
            data: {
                totalUsers,
                activeUsers,
                connectedUsers,
                totalBroadcasts,
                adminKey: admin.loginKey,
                targetMessage: admin.targetMessage
            }
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Generate/Reset admin login key
router.post('/generate-key', authenticateAdmin, async (req, res) => {
    try {
        const newKey = uuidv4().substring(0, 8).toUpperCase();
        await Admin.findByIdAndUpdate(req.adminId, { loginKey: newKey });
        res.json({ success: true, key: newKey });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Set target message
router.post('/set-message', authenticateAdmin, async (req, res) => {
    try {
        const { message } = req.body;
        await Admin.findByIdAndUpdate(req.adminId, { targetMessage: message });
        res.json({ success: true, message: 'Target message updated' });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get target message
router.get('/target-message', authenticateAdmin, async (req, res) => {
    try {
        const admin = await Admin.findById(req.adminId);
        res.json({ success: true, message: admin.targetMessage });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get all users
router.get('/users', authenticateAdmin, async (req, res) => {
    try {
        const users = await User.find().select('-password').sort({ createdAt: -1 });
        res.json({ success: true, users });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Toggle user active status
router.post('/users/toggle-status', authenticateAdmin, async (req, res) => {
    try {
        const { userId, isActive } = req.body;
        await User.findByIdAndUpdate(userId, { isActive });
        res.json({ success: true });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Delete user
router.delete('/users/:userId', authenticateAdmin, async (req, res) => {
    try {
        await User.findByIdAndDelete(req.params.userId);
        await Broadcast.deleteMany({ userId: req.params.userId });
        res.json({ success: true });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Get all broadcasts
router.get('/broadcasts', authenticateAdmin, async (req, res) => {
    try {
        const broadcasts = await Broadcast.find()
            .populate('userId', 'username name')
            .sort({ createdAt: -1 })
            .limit(100);
        res.json({ success: true, broadcasts });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

module.exports = router;