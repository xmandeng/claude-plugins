/* @jsx h */
import { test, expect } from 'claude-code/testing'

const reply = (text: string) => ({ component: 'AssistantMessage' as const, props: { text, isFirstOfReply: true } })

// stands in for the engine's own drawing of a reply, beneath the plugin
const engineDraws = (on: any) =>
  on('ui.render', { component: 'AssistantMessage' }, async ($: any, e: any) => {
    const { Text } = await $.ui.resolve(e)
    return <Text>{`engine: ${e.props.text}`}</Text>
  })

test('openLinks makes the links in a reply pressable', { options: { openLinks: true } }, async ($, on) => {
  engineDraws(on)
  const ui = await $.ui.mount({ plugin: 'webview', surface: 'terminal', ...reply('See https://example.com/docs.') })
  expect(await ui.find({ key: 'webview-links' })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /^engine: / })).toBeUndefined()
  await ui.unmount()
})

test('openLinks leaves a reply without web links to the engine', { options: { openLinks: true } }, async ($, on) => {
  engineDraws(on)
  const ui = await $.ui.mount({ plugin: 'webview', surface: 'terminal', ...reply('No links here.') })
  expect(await ui.find({ key: 'webview-links' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^engine: / })).toBeDefined()
  await ui.unmount()
})

test('replies are left to the engine when openLinks is off', async ($, on) => {
  engineDraws(on)
  const ui = await $.ui.mount({ plugin: 'webview', surface: 'terminal', ...reply('See https://example.com') })
  expect(await ui.find({ key: 'webview-links' })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /^engine: / })).toBeDefined()
  await ui.unmount()
})
