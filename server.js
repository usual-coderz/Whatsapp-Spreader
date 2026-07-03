const express = require('express');
const mongoose = require('mongoose');
const dotenv = require('dotenv');
const path = require('path');
const http = require('http');
const socketIO = require('socket.io');
const cookieParser = require('cookie-parser');
const cors = require('cors');

dotenv.config();

const app = express();
const server = http.createServer(app);
const io = socketIO(server);

// MongoDB Connection
mongoose.connect(process.env.MONGODB_URI, {
    useNewUrlParser: true,
    useUnifiedTopology: true
}).then(() => {
    console.log('✅ MongoDB Connected');
    initAdmin();
}).catch(err => {
    console.error('❌ MongoDB Connection Error:', err);
    process.exit(1);
});

const bcrypt = require('bcryptjs');
const Admin = require('./models/Admin');

async function initAdmin() {
    try {
        const adminExists = await Admin.findOne({ email: process.env.ADMIN_EMAIL });
        if (!adminExists) {
            const hashedPassword = await bcrypt.hash(process.env.ADMIN_PASSWORD, 10);
            await Admin.create({
                email: process.env.ADMIN_EMAIL,
                password: hashedPassword,
                name: 'Super Admin'
            });
            console.log('✅ Default Admin Created');
        }
    } catch (err) {
        console.error('Admin init error:', err);
    }
}

// Middleware
app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(cookieParser());
app.use(cors());
app.use(express.static(path.join(__dirname, 'public')));
app.set('view engine', 'ejs');

// Make io accessible to routes
app.set('io', io);

// Global whatsapp clients map
const whatsappClients = {};
app.set('whatsappClients', whatsappClients);

// Routes
app.use('/api/auth', require('./routes/auth'));
app.use('/api/admin', require('./routes/admin'));
app.use('/api/user', require('./routes/user'));

// Serve HTML views
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'views', 'login.html')));
app.get('/admin/*', (req, res) => res.sendFile(path.join(__dirname, 'views', 'admin', 'dashboard.html')));
app.get('/user/*', (req, res) => res.sendFile(path.join(__dirname, 'views', 'user', 'dashboard.html')));

// Socket.IO
io.on('connection', (socket) => {
    console.log('🔌 Client connected:', socket.id);

    socket.on('join-qr-room', (userId) => {
        socket.join(`qr_${userId}`);
    });

    socket.on('disconnect', () => {
        console.log('🔌 Client disconnected:', socket.id);
    });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Server running on http://0.0.0.0:${PORT}`);
});