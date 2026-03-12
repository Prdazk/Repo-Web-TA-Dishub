module.exports = {
    apps: [{
        name: "cctv-hls",
        script: "src/server.ts",
        interpreter: "node",
        interpreter_args: "-r ts-node/register",
        exec_mode: "fork",
        watch: false,
        env: {
            NODE_ENV: "development"
        }
    }]
}