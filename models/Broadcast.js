const mongoose = require('mongoose');

const BroadcastSchema = new mongoose.Schema({
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true },
    message: { type: String, required: true },
    totalNumbers: { type: Number, default: 0 },
    successCount: { type: Number, default: 0 },
    failCount: { type: Number, default: 0 },
    status: { type: String, enum: ['pending', 'running', 'completed', 'stopped'], default: 'pending' },
    startedAt: { type: Date },
    completedAt: { type: Date },
    createdAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('Broadcast', BroadcastSchema);