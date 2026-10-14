// Prints the verifyChain result for a chain file given as argv[2], using ui/chain.js unchanged.
const fs = require("fs");
const path = require("path");
const { verifyChain } = require(path.join(__dirname, "..", "..", "ui", "chain.js"));
const records = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
verifyChain(records).then((v) => console.log(JSON.stringify(v)));
