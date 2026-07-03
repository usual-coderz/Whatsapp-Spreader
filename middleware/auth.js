const jwt = require('jsonwebtoken');

function authenticateAdmin(req, res, next) {
    const token = req.cookies?.admin_token || req.headers?.authorization?.split(' ')[1];
    if (!token) {
        return res.status(401).json({ success: false, message: 'Unauthorized' });
    }
    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);
        req.adminId = decoded.id;
        req.userRole = 'admin';
        next();
    } catch (err) {
        return res.status(401).json({ success: false, message: 'Invalid token' });
    }
}

function authenticateUser(req, res, next) {
    const token = req.cookies?.user_token || req.headers?.authorization?.split(' ')[1];
    if (!token) {
        return res.status(401).json({ success: false, message: 'Unauthorized' });
    }
    try {
        const decoded = jwt.verify(token, process.env.JWT_SECRET);
        req.userId = decoded.id;
        req.userRole = 'user';
        next();
    } catch (err) {
        return res.status(401).json({ success: false, message: 'Invalid token' });
    }
}

module.exports = { authenticateAdmin, authenticateUser };