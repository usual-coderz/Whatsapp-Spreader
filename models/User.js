const mongoose = require('mongoose');

const userSchema = new mongoose.Schema({
  name: { type: String, required: true },
  loginKey: { type: String, required: true, unique: true },
  password: { type: String, required: true },
  createdBy: { type: String, default: 'admin' },
  status: { type: String, enum: ['active', 'inactive'], default: 'active' },
  whatsappStatus: { type: String, enum: ['disconnected', 'connected', 'qr_ready'], default: 'disconnected' },
  sessionData: { type: Object, default: null },
  stats: {
    totalSent: { type: Number, default: 0 },
    totalFailed: { type: Number, default: 0 },
    lastBroadcast: { type: Date, default: null }
  },
  createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('User', userSchema);