import test from "node:test"; import assert from "node:assert/strict"; import { formatOutput, resolveFormat } from "../src/output.js";
const data={success:true,empty:"",settings:{x:1},items:[{name:"a",count:2}]};
test("formats json table csv and markdown",()=>{assert.equal(formatOutput(data,"json"),JSON.stringify(data));assert.match(formatOutput(data,"table"),/name/);assert.match(formatOutput(data,"csv"),/name,count/);assert.match(formatOutput(data,"markdown"),/\| Key \| Value \|/)});
test("pretty and clean",()=>{assert.equal(resolveFormat({pretty:true}),"pretty");assert.doesNotMatch(formatOutput(data,"json",{clean:true}),/settings|success|empty/)});
