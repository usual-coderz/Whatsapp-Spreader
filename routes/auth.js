const express = require('express');
const router = express.Router();
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const Admin = require('../models/Admin');
const User = require('../models/User');

// Admin Login
router.post('/admin/login', async (req, res) => {
    try {
        const { email, password } = req.body;
        
        const admin = await Admin.findOne({ email });
        if (!admin) {
            return res.status(400).json({ success: false, message: 'Invalid credentials' });
        }

        const isMatch = await bcrypt.compare(password, admin.password);
        if (!isMatch) {
            return res.status(400).json({ success: false, message: 'Invalid credentials' });
        }

        const token = jwt.sign(
            { id: admin._id, role: 'admin' },
            process.env.JWT_SECRET,
            { expiresIn: '24h' }
        );

        res.cookie('admin_token', token, {
            httpOnly: true,
            maxAge: 24 * 60 * 60 * 1000
        });

        res.json({
            success: true,
            token,
            admin: { id: admin._id, name: admin.name, email: admin.email }
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// User Login with Key
router.post('/user/login', async (req, res) => {
    try {
        const { username, password, loginKey } = req.body;

        // Verify login key first
        const admin = await Admin.findOne({ loginKey });
        if (!admin) {
            return res.status(400).json({ success: false, message: 'Invalid login key' });
        }

        // Find or create user
        let user = await User.findOne({ username, loginKey });
        
        if (!user) {
            // New user registration
            const hashedPassword = await bcrypt.hash(password, 10);
            user = new User({
                username,
                password: hashedPassword,
                name: username,
                loginKey
            });
            await user.save();
        } else {
            // Existing user - verify password
            const isMatch = await bcrypt.compare(password, user.password);
            if (!isMatch) {
                return res.status(400).json({ success: false, message: 'Invalid credentials' });
            }
        }

        if (!user.isActive) {
            return res.status(403).json({ success: false, message: 'Account is deactivated' });
        }

        const token = jwt.sign(
            { id: user._id, role: 'user' },
            process.env.JWT_SECRET,
            { expiresIn: '24h' }
        );

        res.cookie('user_token', token, {
            httpOnly: true,
            maxAge: 24 * 60 * 60 * 1000
        });

        res.json({
            success: true,
            token,
            user: {
                id: user._id,
                name: user.name,
                username: user.username,
                whatsappConnected: user.whatsappConnected,
                whatsappNumber: user.whatsappNumber,
                totalSent: user.totalSent,
                totalFailed: user.totalFailed,
                isBroadcasting: user.isBroadcasting
            }
        });
    } catch (error) {
        res.status(500).json({ success: false, message: error.message });
    }
});

// Check auth status
router.get('/status', (req, res) => {
    const adminToken = req.cookies?.admin_token;
    const userToken = req.cookies?.user_token;

    if (adminToken) {
        try {
            const decoded = jwt.verify(adminToken, process.env.JWT_SECRET);
            return res.json({ authenticated: true, role: 'admin', userId: decoded.id });
        } catch (e) {}
    }

    if (userToken) {
        try {
            const decoded = jwt.verify(userToken, process.env.JWT_SECRET);
            return res.json({ authenticated: true, role: 'user', userId: decoded.id });
        } catch (e) {}
    }

    res.json({ authenticated: false });
});

// Logout
router.post('/logout', (req, res) => {
    res.clearCookie('admin_token');
    res.clearCookie('user_token');
    res.json({ success: true, message: 'Logged out' });
});

module.exports = router;