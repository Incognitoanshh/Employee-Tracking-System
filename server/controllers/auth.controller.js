const pool = require("../config/db");
const jwt = require("jsonwebtoken");

exports.login = async (req, res) => {

    console.log("LOGIN HIT");
    console.log("BODY RECEIVED:", req.body);

    const { username, password } = req.body;

    if (!username || !password) {
        return res.status(400).json({
            success: false,
            message: "username and password are required"
        });
    }

    try {

        const result = await pool.query(
            "SELECT * FROM employees WHERE username = $1",
            [username]
        );

        if (result.rows.length === 0) {
            return res.status(401).json({
                success: false,
                message: "Invalid credentials"
            });
        }

        const employee = result.rows[0];

        // Plaintext compare (DB mein hash nahi hai)
        if (password !== employee.password) {
            return res.status(401).json({
                success: false,
                message: "Invalid credentials"
            });
        }

        const token = jwt.sign(
            {
                employee_id: employee.employee_id,
                role: employee.role
            },
            process.env.JWT_SECRET,
            { expiresIn: "8h" }
        );

        return res.status(200).json({
            success: true,
            employee_id: employee.employee_id,
            role: employee.role,
            token
        });

    } catch (error) {
        console.log("LOGIN ERROR:", error);
        return res.status(500).json({
            success: false,
            message: "Server error"
        });
    }

};
