# Her keys

Jasmine, the assistant in PianaSoul, needs an API key of your own, which you enter in Settings › Advanced when you select Assistant. Keys are stored
in the keychain on your Mac, and what you use is billed to your own account with each service. Each key's window has a
How to Get a Key… button with these steps, checked against each service's own documentation on 2026-11-10 (the consoles
change: if a button is not where a step says, the service's own help is right).

- **Model**, for conversation, speech recognition and naming pieces: OpenAI, or Alibaba Cloud Model Studio where
  OpenAI is out of reach, as in China mainland. A ChatGPT subscription does not include the OpenAI API.
- **Voice:** the system voice needs nothing. ElevenLabs sounds far more natural and needs a paid plan; Alibaba
  Cloud Model Studio has a voice of its own. Model and voice are chosen separately, each with its own key.

Online Search & Transcription needs no key: it runs on your Mac.

## OpenAI

Needed when Model is set to OpenAI. OpenAI's list of supported countries and territories doesn't include China
mainland, Hong Kong, or Macao; use Alibaba Cloud Model Studio there.

1. Open [platform.openai.com/api-keys](https://platform.openai.com/api-keys) and sign in, or sign up for an OpenAI
   account.
2. Add credit first: open the [API billing overview](https://platform.openai.com/account/billing/overview), click Add
   payment details, and enter your payment information.
3. Choose an amount: the minimum is $5 and the default is $10. The Use auto-reload option is on by default; turn it
   off if you don't want automatic purchases.
4. Confirm the purchase, then wait a few minutes for the balance to update.
5. Go back to the API keys page, choose a project (every account has a Default project), and click Create new secret
   key.
6. Name the key, for example PianaSoul, and leave Permissions on All.
7. Create the key and copy it right away. It's shown only once; if you lose it, create a new one.
8. In PianaSoul, open Settings › Advanced, select Assistant, and click Configure… next to OpenAI in the Model row.
9. Paste the key and click Verify and Save.

Optional: to guard against overspending, set a Monthly spend limit under Limits for the project and turn on Enforce a
hard limit. Once it's reached, requests fail and she can't be used.

What it costs: a ChatGPT subscription is billed separately from the API, and API usage is paid from prepaid credit.
The conversation model, gpt-6-luna, is $0.10 per million input tokens and $0.50 per million output tokens; speech
recognition, gpt-transcribe, is $0.0045 per minute ([pricing](https://developers.openai.com/api/docs/pricing)). The
minimum credit purchase is $5, and credits expire after one year ([prepaid
billing](https://help.openai.com/en/articles/8264644-setting-up-and-managing-prepaid-api-billing)).

## ElevenLabs

Optional, for her voice; without it she uses the system voice. It needs a paid ElevenLabs plan: PianaSoul's voices
come from the voice library, and a free account can't use voices from the library through the API.

1. Open [elevenlabs.io](https://elevenlabs.io) and click Sign up to create an account, or Log in if you already have
   one.
2. Subscribe to a paid plan: open [elevenlabs.io/pricing](https://elevenlabs.io/pricing) and click Choose Starter on
   the Starter plan.
3. Pay with a credit card, Apple Pay, or Google Pay. Prepaid cards aren't directly supported.
4. In the left sidebar, click Developers, then select the API Keys tab.
5. Click Create Key.
6. Name the key, for example PianaSoul. Keep Restrict Key on: keys are restricted by default, so they allow only the
   features you choose.
7. Set Text to Speech to Access and Voices to Write (PianaSoul adds the voice you choose to your account), and leave
   everything else on No Access.
8. After you create the key, copy it right away: the full key is shown only once.
9. In PianaSoul, open Settings › Advanced, select Assistant, click Configure… next to ElevenLabs in the Voice row,
   paste the key, choose a voice, and click Verify and Save.

What it costs: the Starter plan is $6 a month. Through the API it includes 150,000 characters of v4 Turbo; beyond
that, v4 Turbo is $0.04 per 1,000 characters ([API pricing](https://elevenlabs.io/pricing/api)).

## Alibaba Cloud Model Studio

Needed when Model is set to Alibaba Cloud, and optional for Voice. Alibaba Cloud has two sites, and a key works only
in the region it was made in.

Which site to use:

- When your Mac's region is China mainland (System Settings › General › Language & Region), Get an Alibaba Cloud API
  Key in the PianaSoul window opens the China site; with any other region, including Hong Kong, Macao, and Taiwan, it
  opens the international site.
- If you have a phone number and a personal Alipay account that can complete real-name verification, use the China
  site. If not, use the international site: it needs a phone number outside China mainland that can receive SMS, and a
  bank card.
- The two sites have separate accounts. PianaSoul checks a key in the Beijing region first and, if it doesn't work
  there, in the Singapore region, so you don't choose one. Paste a key in both the Model and Voice rows; it can be the
  same key.

### China site (bailian.console.aliyun.com, Beijing region)

The console is in Chinese. The button names below are translated from the Chinese labels.

1. Sign up for an Alibaba Cloud account: open [aliyun.com](https://www.aliyun.com), click the login/register button at
   the top right, then click Register.
2. Enter a mobile number and the SMS verification code, agree to the terms, and click Register Now.
3. Verify your identity (required before adding funds or activating Model Studio): open the [account verification
   page](https://myaccount.console.aliyun.com/certificate), click Personal Alipay Verification in the personal area,
   scan the code with the Alipay app on your phone, and agree to the authorization.
4. With your Alibaba Cloud main account, open the [Model Studio
   console](https://bailian.console.aliyun.com/model/market), then read and agree to the service agreement. Model
   Studio is activated automatically. If no agreement appears, it's already activated.
5. Open the [API Key page](https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key), make sure the region
   at the top right is China (Beijing), and click Create API Key.
6. In the dialog, keep the defaults: the owner is your Alibaba Cloud main account and the workspace is the default
   workspace. Set Permissions to All, then click OK.
7. Copy the key right away: a new API key is shown in full only once. Don't use a key that starts with `sk-sp-`; it's
   a dedicated Coding Plan or Token Plan key, which PianaSoul can't use.
8. Add funds once the free quota is used up: open the [recharge
   page](https://billing-cost.console.aliyun.com/fortune/fund-management/recharge), choose Alipay or UnionPay online
   payment, enter an amount, agree to the recharge agreement, and click Confirm Recharge. Pay with an Alipay account
   or debit card in the name verified for the account; Alipay doesn't accept Huabei or credit cards, and UnionPay
   online payment doesn't accept credit cards.

What it costs: Model Studio charges by the token, billed to your Alibaba Cloud account. New users get a free quota in
the Beijing region only: usually 1 million tokens for each model, valid for 90 days, granted automatically when Model
Studio is activated after real-name verification ([free
quota](https://help.aliyun.com/zh/model-studio/new-free-quota)). After that, pay-as-you-go prices apply: the
conversation model, deepseek-v4.1-flash, is ¥2 per million input tokens and ¥8 per million output tokens from 8:00 to
22:00 Beijing time, and half that from 22:00 to 8:00; speech recognition, qwen-audio-3.1-asr-flash, is ¥0.8 per
million input tokens and ¥2.7 per million output tokens; the voice, qwen-audio-3.1-tts-flash, is ¥1.5 per million
input tokens and ¥12 per million output tokens ([pricing](https://help.aliyun.com/zh/model-studio/model-pricing)). The
official pages don't make clear whether deepseek-v4.1-flash gets the free quota, so don't count on it. If you turn on
Free Quota Only, a model stops answering once its free quota is used up.

### International site (alibabacloud.com, Singapore region)

1. Open [alibabacloud.com](https://www.alibabacloud.com), click Sign In at the top right, then click Register.
2. Enter your email address and a password, choose an Account Type (Individual for personal use) and your country or
   region. The country or region sets your billing currency and tax rates and can't be changed after registration.
3. Verify your mobile number or email address, accept the terms, and click Register.
4. Complete security authentication and add payment information. A bank card needs online and international payments
   turned on, and gets a $1.00 pre-authorization (for verification only). Prepaid cards, virtual cards, gift cards,
   and credit cards issued in China mainland aren't supported.
5. Open the [Model Studio console (Singapore)](https://modelstudio.console.alibabacloud.com/ap-southeast-1), then read
   and agree to the service agreement. Model Studio is activated automatically. If no agreement appears, it's already
   activated.
6. Open the [API Key page](https://modelstudio.console.alibabacloud.com/ap-southeast-1/settings/api-key), make sure
   the region at the top right is Singapore, and click Create API Key.
7. For Workspace, choose the default workspace, set Permissions to All, and click OK.
8. Copy the key right away: after you close the dialog, the full key can't be shown again. Don't use a key that starts
   with `sk-sp-`.

What it costs: Model Studio charges by the token, billed to your Alibaba Cloud account. The free quota is available
only in the Singapore region: usually 1 million tokens for each model, valid for 90 days, and your account information
must be complete first ([free quota](https://www.alibabacloud.com/help/en/model-studio/new-free-quota)). Prices per
million tokens: deepseek-v4.1-flash is $0.30 for input and $1.20 for output during busy hours (8:00 to 22:00 Beijing
time) and half that otherwise; qwen-audio-3.1-asr-flash is $0.15 for input and $0.47 for output;
qwen-audio-3.1-tts-flash is $0.23 for input and $1.87 for output
([pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing)). The official pages don't make clear
whether deepseek-v4.1-flash gets the free quota, so don't count on it.

### In PianaSoul

1. In PianaSoul, open Settings › Advanced, select Assistant, click Configure… next to Alibaba Cloud in the Model row,
   paste the key, and click Verify and Save.
2. To use Alibaba Cloud's voice, do the same in the Voice row: paste the same key or another one, choose a voice, and
   click Verify and Save.
