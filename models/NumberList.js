const mongoose = require('mongoose');

const NumberListSchema = new mongoose.Schema({
    userId: { type: mongoose.Schema.Types.ObjectId, ref: 'User', required: true },
    filename: { type: String, default: 'numbers.txt' },
    numbers: [{ type: String }],
    uploadedAt: { type: Date, default: Date.now }
});

module.exports = mongoose.model('NumberList', NumberListSchema);