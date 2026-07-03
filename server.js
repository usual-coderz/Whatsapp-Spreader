require('dotenv').config();
const express = require('express');
const mongoose = require('mongoose');
const bcrypt = require('bcrypt');
const jwt = require('jsonwebtoken');
const cors = require('cors');
const http = require('http');
const { Server } = require('socket.io');
const path = require('path');
const fs = require('fs');
const { v4: uuidv4 } = require('uuid');

const User = require('./models/User');
const Broadcast = require('./models/Broadcast');
const clientManager = require('./whatsapp/clientManager');

const app = express();
const server = http.createServer(app);
const io = new Server(server, {
  cors: {
    origin: '*',
    methods: ['GET', 'POST']
  }
});

app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'views')));

// ============ MIDDLEWARE ============
const authenticate = (req, res, next) => {
  const authHeader = req.headers.authorization;
  if (!authHeader) return res.status(401).json({ error: 'No token provided' });

  const token = authHeader.split(' ')[1];
  if (!token) return res.status(401).json({ error: 'Invalid token format' });

  try {
    req.user = jwt.verify(token, process.env.JWT_SECRET);
    next();
  } catch (e) {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
};

const adminOnly = (req, res, next) => {
  if (req.user.role !== 'admin') {
    return res.status(403).json({ error: 'Admin access required' });
  }
  next();
};

// ============ SOCKET.IO ============
io.on('connection', (socket) => {
  const token = socket.handshake.auth?.token;
  if (!token) {
    console.log('⚠️ Socket connection rejected: no token');
    return socket.disconnect();
  }

  try {
    const user = jwt.verify(token, process.env.JWT_SECRET);
    const room = `user_${user.userId}`;
    socket.join(room);
    socket.userId = user.userId;
    socket.role = user.role;
    console.log(`🔌 Socket connected: ${user.userId} (${user.role})`);

    socket.on('disconnect', () => {
      console.log(`🔌 Socket disconnected: ${user.userId}`);
    });
  } catch (e) {
    console.log('⚠️ Socket connection rejected: invalid token');
    socket.disconnect();
  }
});

// ============ AUTH ROUTES ============
app.post('/api/auth/login', async (req, res) => {
  try {
    const { loginKey, password } = req.body;

    if (!loginKey || !password) {
      return res.status(400).json({ error: 'Login key and password required' });
    }

    // Admin login
    if (loginKey === process.env.ADMIN_LOGIN_KEY) {
      if (password !== process.env.ADMIN_PASSWORD) {
        return res.status(401).json({ error: 'Invalid admin credentials' });
      }
      const token = jwt.sign(
        { userId: 'admin', role: 'admin' },
        process.env.JWT_SECRET,
        { expiresIn: '24h' }
      );
      return res.json({ token, role: 'admin', name: 'Admin' });
    }

    // User login
    const user = await User.findOne({ loginKey, status: 'active' });
    if (!user) {
      return res.status(401).json({ error: 'Invalid login key' });
    }

    const match = await bcrypt.compare(password, user.password);
    if (!match) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    const token = jwt.sign(
      { userId: user._id.toString(), role: 'user', name: user.name },
      process.env.JWT_SECRET,
      { expiresIn: '24h' }
    );

    res.json({ token, role: 'user', name: user.name });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ error: 'Server error' });
  }
});

// ============ ADMIN ROUTES ============
// Create user (with unique login key)
app.post('/api/admin/create-user', authenticate, adminOnly, async (req, res) => {
  try {
    const { name, password } = req.body;
    if (!name || !password) {
      return res.status(400).json({ error: 'Name & password required' });
    }

    // Generate unique login key
    const loginKey = `USR-${uuidv4().slice(0, 8).toUpperCase()}`;

    const hashedPassword = await bcrypt.hash(password, 10);
    const user = new User({ name, loginKey, password: hashedPassword });
    await user.save();

    res.json({
      message: 'User created',
      user: {
        name: user.name,
        loginKey: user.loginKey,
        _id: user._id
      }
    });
  } catch (err) {
    console.error('Create user error:', err);
    res.status(500).json({ error: 'Failed to create user' });
  }
});

// Get all users
app.get('/api/admin/users', authenticate, adminOnly, async (req, res) => {
  try {
    const users = await User.find({}, { sessionData: 0, password: 0 });
    res.json(users);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch users' });
  }
});

// Set target message (admin sets the broadcast message template)
app.post('/api/admin/set-message', authenticate, adminOnly, async (req, res) => {
  try {
    const { message } = req.body;
    if (!message) return res.status(400).json({ error: 'Message required' });

    // Store in a global variable (for production, use a DB collection)
    process.env.TARGET_MESSAGE = message;
    res.json({ message: 'Target message set', targetMessage: message });
  } catch (err) {
    res.status(500).json({ error: 'Failed to set message' });
  }
});

// Get current target message
app.get('/api/admin/get-message', authenticate, adminOnly, (req, res) => {
  res.json({ targetMessage: process.env.TARGET_MESSAGE || '' });
});

// Delete user
app.delete('/api/admin/users/:id', authenticate, adminOnly, async (req, res) => {
  try {
    // Destroy WhatsApp client if exists
    await clientManager.destroyClient(req.params.id);
    await User.findByIdAndDelete(req.params.id);
    res.json({ message: 'User deleted' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to delete user' });
  }
});

// ============ USER ROUTES ============
// Get user's own data
app.get('/api/user/profile', authenticate, async (req, res) => {
  if (req.user.role === 'admin') {
    return res.json({ role: 'admin' });
  }
  try {
    const user = await User.findById(req.user.userId, { sessionData: 0, password: 0 });
    res.json(user);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch profile' });
  }
});

// Connect WhatsApp
app.post('/api/user/connect', authenticate, async (req, res) => {
  if (req.user.role === 'admin') {
    return res.status(400).json({ error: 'Admin cannot connect WhatsApp' });
  }
  try {
    const userId = req.user.userId;
    await clientManager.createClient(userId, io);
    res.json({ message: 'Connecting...' });
  } catch (err) {
    console.error('Connect error:', err);
    res.status(500).json({ error: 'Failed to connect' });
  }
});

// Get WhatsApp status
app.get('/api/user/status', authenticate, async (req, res) => {
  if (req.user.role === 'admin') {
    return res.json({ status: 'admin' });
  }
  try {
    const status = clientManager.getStatus(req.user.userId);
    const user = await User.findById(req.user.userId);
    if (status.status !== user.whatsappStatus) {
      user.whatsappStatus = status.status;
      await user.save();
    }
    res.json(status);
  } catch (err) {
    res.status(500).json({ error: 'Failed to get status' });
  }
});

// Disconnect WhatsApp
app.post('/api/user/disconnect', authenticate, async (req, res) => {
  if (req.user.role === 'admin') {
    return res.status(400).json({ error: 'N/A' });
  }
  try {
    await clientManager.destroyClient(req.user.userId);
    await User.findByIdAndUpdate(req.user.userId, {
      whatsappStatus: 'disconnected',
      sessionData: null
    });
    res.json({ message: 'Disconnected' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to disconnect' });
  }
});

// Get target message (user reads what admin set)
app.get('/api/user/target-message', authenticate, async (req, res) => {
  res.json({ targetMessage: process.env.TARGET_MESSAGE || 'No message set by admin' });
});

// Start broadcast
app.post('/api/user/start-broadcast', authenticate, async (req, res) => {
  if (req.user.role === 'admin') {
    return res.status(400).json({ error: 'Admin cannot broadcast' });
  }

  try {
    const numbersPath = path.join(__dirname, 'numbers.txt');
    if (!fs.existsSync(numbersPath)) {
      return res.status(400).json({ error: 'numbers.txt not found' });
    }

    const message = process.env.TARGET_MESSAGE;
    if (!message) {
      return res.status(400).json({ error: 'Admin has not set a target message yet' });
    }

    const numbers = fs.readFileSync(numbersPath, 'utf-8')
      .split('\n')
      .map(n => n.trim())
      .filter(n => n.length > 0);

    if (numbers.length === 0) {
      return res.status(400).json({ error: 'numbers.txt is empty' });
    }

    const result = await clientManager.startBroadcast(
      req.user.userId,
      message,
      numbers,
      io,
      Broadcast
    );

    if (result && result.error) {
      return res.status(400).json(result);
    }

    // Update user stats
    await User.findByIdAndUpdate(req.user.userId, {
      $inc: {
        'stats.totalSent': result.success || 0,
        'stats.totalFailed': result.fail || 0
      },
      'stats.lastBroadcast': new Date()
    });

    res.json(result);
  } catch (err) {
    console.error('Broadcast error:', err);
    res.status(500).json({ error: 'Broadcast failed' });
  }
});

// Stop broadcast
app.post('/api/user/stop-broadcast', authenticate, async (req, res) => {
  try {
    clientManager.stopBroadcast(req.user.userId);
    res.json({ message: 'Broadcast stopped' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to stop broadcast' });
  }
});

// Get broadcast history
app.get('/api/user/history', authenticate, async (req, res) => {
  try {
    if (req.user.role === 'admin') {
      const history = await Broadcast.find()
        .sort({ createdAt: -1 })
        .limit(50)
        .populate('userId', 'name loginKey');
      return res.json(history);
    }

    const history = await Broadcast.find({ userId: req.user.userId })
      .sort({ createdAt: -1 })
      .limit(50);
    res.json(history);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch history' });
  }
});

// ============ HEALTH CHECK ============
app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    uptime: process.uptime(),
    timestamp: new Date().toISOString()
  });
});

// ============ DEFAULT ROUTE ============
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'views', 'login.html'));
});

// ============ ERROR HANDLER ============
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({ error: 'Internal server error' });
});

// ============ START SERVER ============
const PORT = process.env.PORT || 5000;

mongoose.connect(process.env.MONGODB_URI)
  .then(() => {
    console.log('✅ MongoDB connected');

    server.listen(PORT, '0.0.0.0', () => {
      console.log(`\n🚀 Server running on http://0.0.0.0:${PORT}`);
      console.log(`📡 Admin Panel: http://localhost:${PORT}/admin.html`);
      console.log(`🔑 Admin Login Key: ${process.env.ADMIN_LOGIN_KEY}`);
    });
  })
  .catch(err => {
    console.error('❌ MongoDB connection error:', err);
    process.exit(1);
  });