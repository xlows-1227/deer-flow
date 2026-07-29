# 智能体接口 API 文档

> 模块：`AgentApiController`  
> 基础路径：`/api/v1/agent`  
> 服务：`datacenter-web-service`（本地默认端口 `8087`）

---

## 通用说明

### 统一响应结构 `DataResult<T>`

| 字段 | 类型 | 说明 |
|------|------|------|
| code | Integer | 业务状态码，成功为 `-9999800` |
| msg | String | 提示信息，成功为 `success` |
| result | T | 业务数据 |
| cost | Integer | 接口耗时（毫秒，可能为空） |

### 成功示例外壳

```json
{
  "code": -9999800,
  "msg": "success",
  "result": {},
  "cost": null
}
```

### 常见错误码

| code | 含义 | 典型场景 |
|------|------|----------|
| 4009 | 参数值不合法 | `secretId` 为空/无效、客户端已禁用、`apiId` 为空 |
| 4010 | 找不到对象 | 接口不存在或已禁用 |
| 4023 | 没有权限 | 未允许智能体调用、接口未发布、客户端无该接口权限 |

---

## 1. 查询智能体可用接口列表

根据客户端密钥 `secretId`，返回该客户端可被智能体调用的接口列表。

### 基本信息

| 项 | 内容 |
|----|------|
| 接口名称 | 根据密钥id查询智能体可用接口列表 |
| URL | `GET /api/v1/agent/apis` |
| 是否鉴权登录 | 否（通过 `secretId` 识别客户端） |

### 请求参数（Query）

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| secretId | String | 是 | 客户端密钥 ID |

### 请求示例

```http
GET /api/v1/agent/apis?secretId=xxxxxxxxxxxxxxxx HTTP/1.1
Host: localhost:8087
```

```bash
curl -G "http://localhost:8087/api/v1/agent/apis" \
  --data-urlencode "secretId=xxxxxxxxxxxxxxxx"
```

### 过滤规则（服务端）

仅返回同时满足以下条件的接口：

1. 客户端存在且未禁用  
2. 客户端与接口已绑定，且绑定关系未禁用  
3. 接口未禁用（`disabled = 0`）  
4. 接口已发布（`status = 0`）  
5. 接口允许智能体调用（`allow_agent_call = 1`）

### 响应字段 `result: AgentApiInfoRes[]`

| 字段 | 类型 | 说明 |
|------|------|------|
| apiId | Integer | 接口 ID |
| apiName | String | 接口名称 |
| apiDesc | String | 接口描述 |

### 成功响应示例

```json
{
  "code": -9999800,
  "msg": "success",
  "result": [
    {
      "apiId": 1001,
      "apiName": "门店销量查询",
      "apiDesc": "按门店编码查询近7天销量"
    },
    {
      "apiId": 1002,
      "apiName": "门店基础信息",
      "apiDesc": "查询门店基础属性"
    }
  ],
  "cost": null
}
```

### 空结果示例

当客户端有效但无可调用接口时，返回空数组：

```json
{
  "code": -9999800,
  "msg": "success",
  "result": [],
  "cost": null
}
```

### 错误响应示例

```json
{
  "code": 4009,
  "msg": "参数值不合法: secretId无效",
  "result": null,
  "cost": null
}
```

---

## 2. 查询接口入参/出参

根据 `secretId` 与 `apiId`，返回指定接口的请求参数与响应参数定义。调用前会校验客户端对该接口的智能体访问权限。

### 基本信息

| 项 | 内容 |
|----|------|
| 接口名称 | 根据接口id和密钥id查询接口入参出参 |
| URL | `GET /api/v1/agent/params` |
| 是否鉴权登录 | 否（通过 `secretId` 识别客户端） |

### 请求参数（Query）

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| secretId | String | 是 | 客户端密钥 ID |
| apiId | Integer | 是 | 接口 ID |

### 请求示例

```http
GET /api/v1/agent/params?secretId=xxxxxxxxxxxxxxxx&apiId=1001 HTTP/1.1
Host: localhost:8087
```

```bash
curl -G "http://localhost:8087/api/v1/agent/params" \
  --data-urlencode "secretId=xxxxxxxxxxxxxxxx" \
  --data-urlencode "apiId=1001"
```

### 权限校验规则（服务端）

按顺序校验：

1. `apiId` 非空  
2. `secretId` 对应客户端存在且未禁用  
3. 接口存在且未禁用  
4. 接口允许智能体调用（`allowAgentCall = true`）  
5. 接口已发布（`status = 0`）  
6. 客户端已绑定该接口，且绑定未禁用  

### 响应字段 `result: AgentApiParamRes`

| 字段 | 类型 | 说明 |
|------|------|------|
| apiId | Integer | 接口 ID |
| apiName | String | 接口名称 |
| calUrl | String | 业务接口完整调用地址（智能体直接 POST 此 URL） |
| requestParam | ParamDetail[] | 入参定义列表 |
| responseParam | ParamDetail[] | 出参定义列表 |

#### ParamDetail 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| id | String | 参数节点 ID |
| type | String/Enum | 参数类型（如 String、Number、Object、Array 等） |
| name | String | 参数名称 |
| rule | String | 生成/校验规则 |
| value | String | 初始值/示例值 |
| description | String | 参数说明 |
| parentId | String | 父级参数 ID（树形结构） |
| priority | Integer | 排序优先级 |
| encryptionFlag | Boolean | 是否加密，默认 `false` |
| decryptionFlag | Boolean | 是否解密，默认 `false` |
| scope | String | 作用域 |

### 成功响应示例

```json
{
  "code": -9999800,
  "msg": "success",
  "result": {
    "apiId": 1001,
    "apiName": "门店销量查询",
    "calUrl": "http://apiservice/v1/store/data/getSales",
    "requestParam": [
      {
        "id": "1",
        "type": "String",
        "name": "storeCode",
        "rule": "",
        "value": "SH001",
        "description": "门店编码",
        "parentId": null,
        "priority": 1,
        "encryptionFlag": false,
        "decryptionFlag": false,
        "scope": null
      }
    ],
    "responseParam": [
      {
        "id": "2",
        "type": "Number",
        "name": "salesQty",
        "rule": "",
        "value": "",
        "description": "销量数量",
        "parentId": null,
        "priority": 1,
        "encryptionFlag": false,
        "decryptionFlag": false,
        "scope": null
      }
    ]
  },
  "cost": null
}
```

### 错误响应示例

```json
{
  "code": 4023,
  "msg": "没有权限: 该接口不允许智能体调用",
  "result": null,
  "cost": null
}
```

```json
{
  "code": 4023,
  "msg": "没有权限: 客户端没有该接口权限",
  "result": null,
  "cost": null
}
```

```json
{
  "code": 4010,
  "msg": "找不到对象: 接口不存在",
  "result": null,
  "cost": null
}
```

---

## 推荐调用顺序

```text
1) GET /api/v1/agent/apis?secretId=xxx
   -> 获取可调用接口列表（apiId / apiName / apiDesc）

2) GET /api/v1/agent/params?secretId=xxx&apiId=1001
   -> 获取目标接口入参、出参定义与 calUrl，供智能体拼装请求

3) POST calUrl
   -> Header: secretId / timestamp / sign(=secretKey 明文，本项目不做 HMAC)
   -> Body: { paramData, pageSize?, pageNum?, ... }
```

### DeerFlow 连接器约定

- 发现服务 Base URL 由环境变量 `ONEDATA_API_BASE_URL` 配置（例如 `http://share-onedata-api-ent4.prd.yumc.local/v1`）
- 连接器实例仅保存 `secretId` / `secretKey`
- Agent 通过 `call_connector_action`：`onedata.list_apis` → `onedata.get_params` → `onedata.call_api`（直接使用返回的 `calUrl`）
- 本项目不实现字段加解密与 HMAC 签名；`sign` 请求头直接传 `secretKey`
- 下游不可用时可用本地 mock：`cd backend && PYTHONPATH=. uv run python scripts/mock_onedata_server.py`，设置 `ONEDATA_API_BASE_URL=http://127.0.0.1:18087/v1`，凭证 `mock-secret-id` / `mock-secret-key`

---

## 备注

1. 两个接口均不依赖 Sa-Token 登录态，依赖 `secretId` 做客户端身份识别。  
2. 列表接口与参数接口共用同一套「智能体可调用」过滤/鉴权逻辑，保证智能体只能看到并读取已授权接口。  
3. Swagger 可在服务启动后通过项目既有 Swagger UI 查看（tag：`智能体接口`）。



三、接口使用方
1.咨询接口提供方信息
        a.接口的请求路径   

                示例:   http://10.218.221.161:8883/v1/store/data/getStoreOrgFull  

        b.接口的 secretId和secretKey 

               示例: sercrtId:5926768a118b4e749a145187cc1595f0     secretKey:xxxx

2.请求方法  
       Post + Json Body

示例

curl -X POST \
  http://apiservice/v1/xxx/yyy/zzz \
  -H 'Content-Type: application/json' \
  -H 'secretId: 79da37e48f065ec7d57d114b24cc3526' \
  -H 'sign: iUWfegoq7SCkPdAAuD02+uhDZLd2Wvl5bN4aqmck/es=' \
  -H 'timestamp: 1522135633272' \
  -d '{
    "paramData": {
        "queryDay": "2018-04-21",
        "city": "上海市",
        "code": "xxx",
    },
    "pageSize": 20,
    "pageNum": 1,
    "orderBy": "city asc, code desc"
}'


header参数
secretld	secretld	5926768a118b4e749a145187cc1595f0
Content-Type	请求类型	application/json
timestamp	时间戳(当前毫秒级时间戳)	1690539875567
sign	验签(加密方式如下)	dxSWaF6ilM6jc0ojAWIhuKPhAWgTURhCO3KwquM96mc


body参数
paramData	json	是	
自定义参数封装

普通参数需要用paramData封装，json体内部传入参数名称需要和配置的入参一致，否则不生效

pageSize	int	否	查询页数	分页请求需要接口支持分页，pageSize和pageNum需要同时传
pageNum	int	否	每页数据量	分页请求需要接口支持分页，如果接口配置了最大分页数量，取两者最小值
orderBy	string	否	语句排序	
排序请求需要接口支持排序，支持多个字段排序，如果原来sql语句已经有排序不能再使用该参数

maxSize	int	否	最大数据量	非分页接口支持返回最大数据量，默认最大值为10000,取两者最小值
hasTotal	
boolean
否	是否查询总量（仅针对支持分页的接口	默认为true，在查询性能较低且不需要总量的情况，可以将该值传false
示例

{
    "paramData": {
        "queryDay": "2018-04-21",
        "city": "上海市",
        "code": "xxx",
    },
    "pageSize": 20,
    "pageNum": 1,
    "orderBy": "city asc, code desc",
    "maxSize": 15,
    "hasTotal": false,
}


3.返回方法 
返回参数
status	int	返回状态码	200表示成功，其他表示失败，下有具体参考列表
message	string	状态信息	成功为success,否则为其他报错提示信息
cost	int	接口耗时	单位为毫秒
data	json	数据	数据封装对象
     -result	array/object	返回数据	具体返回数据列表，单个对象为object，列表对象为array
     -size	int	数据量	返回array为列表大小，返回object为1或者0
     -total	int	数据总量	
     -pageNum	int	页码	分页接口才有返回
     -pageSize	int	页面大小	分页接口才有返回
示例

{
    "cost": 76,
    "data": {
        "result": [
            {
                "cnt1": 4606,
                "cnt2": 4591
            }
        ],
        "size": 6,
        "total": 512,
        "pageNum": 1,
        "pageSize": 20
    },
    "message": "success",
    "status": 0
}


4.接口鉴权 
JSONObject requestBody ;
...
// 1.1 获取查询参数  paramData中传递的参数
 if (requestBody != null && requestBody.containsKey("paramData")) {
         requestBody.getJSONObject("paramData").entrySet().forEach(
                         data -> requestBody.put(data.getKey(), data.getValue())
         );
         requestBody.remove("paramData");
 
 }
 //需要加入时间戳和secretId
 requestBody.put("timestamp", timestamp);
 requestBody.put("secretId", secretId);
 log.info("筛选请求参数: {}", requestBody);
 
 // 1.2 参数按照字段升序排序（大小写敏感）、拼接请求字符串, 注意数组的话，转成格式为["xx","xxx"], 最后去掉所有空格
 String paramStr = requestBody.entrySet().stream()
                 .sorted(Map.Entry.comparingByKey())
                 .map(data -> data.getKey() + "=" + (EmptyUtils.isEmpty(data.getValue())?"":toString(data.getValue())))
                 .collect(Collectors.joining("&"))
                 .replaceAll(" ", "");
 log.info("拼接后的字符串为: {}", paramStr);
 
 // 1.3 使用 HMAC-SHA256 算法进行签名，再使用base64进行编码
 String sign= BaseEncoding.base64().encode(EncryptUtils.hmacSHA256(paramStr, secretKey.getBytes()));
 log.info("正确签名应为: {}", sign);
 
  
//toString方法
private static String toString(Object obj) {
     if (obj instanceof Collection) {
         return (String) ((Collection) obj).stream().map(v -> v==null?"":v.toString()).collect(Collectors.joining(","));
     } else {
         return obj.toString();
     }
 }
hmac加密
   public static byte[] hmacSHA256(String data, byte[] key) {
    Mac mac = null;
    try {
        mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(key, "HmacSHA256"));
        return mac.doFinal(data.getBytes("UTF-8"));
    } catch (Exception e) {
        throw new CustomException(CustomError.ENCRYPT_ERROR);
    }
}


示例

1.首先申请SecretId和SecretKey
   比如：
    secretId: 79da37e48f065ec7d57d114b24cc3526
    secretKey: c5e979ed9439fcd47989e4314d79eedb
2.筛选参数并排序
  假设你的入参如下：
    {
        "paramData": {
            "paramInt": 1,
            "paramDouble": 3.2,
            "paramStr": "字符串",
            "paramArray": [1,2.56,"字符串"]
        },
        "pageSize": 10,
        "pageNum": 2
    }
   筛选普通参数、分页参数、secretId、当前毫秒级时间戳，并且排序，结果如下：（注意数组的元素之间用逗号进行分隔）
    pageNum=2
    pageSize=10
    paramArray=1,2.56,字符串
    paramDouble=3.2
    paramInt=1
    paramStr=字符串
    secretId=79da37e48f065ec7d57d114b24cc3526
    timestamp=1527134966582
  
3.把上一步排序好的请求参数，格式化成k=v，然后用&拼接在一起，最后去掉所有空格
  拼接后为：
    pageNum=2&pageSize=paramArray=1,2.56&paramDouble=3.2&paramInt=1&paramStr=字符串&secretId=79da37e48f065ec7d57d114b24cc3526&timestamp=1527134966582
4.签名+编码
    使用HMAC-SHA256算法进行签名，再使用base64进行编码，结果为：（这里由于有使用当前时间戳，所以每次运行结果可能不一样，下面结果仅供参考）
    V03Y1SGpJwxJ+RjX9u0VrH99VMvFjSA/TEiHmY2kwqY=


5.字段加解密
代码可以对字段进行加解密配置，如果是同一个字段都配置了加解密，先进行解密再进行加密





解密算法使用自研sdk进行解密，加密需要使用对应sdk进行加密

SecuritySdkUtil ts = new SecuritySdkUtil();
//1：BASE64 2: HEX
String decode = ts.encrypt(dataTmp.get(param).toString(),apiInfo.getDecryptionKey(),1);






加密算法使用aes进行加密，使用方解密如下

public static String AesDecrypt(String code, String aesKey) throws Exception {
    byte[] decode = Base64.getDecoder().decode(code);
    SecretKey key = generateMySQLAESKey(aesKey, "ASCII");
    Cipher cipher = Cipher.getInstance("AES");
    cipher.init(Cipher.DECRYPT_MODE, key);
    byte[] ciphertextBytes = cipher.doFinal(decode);
    return new String(ciphertextBytes, StandardCharsets.UTF_8);
}
 
public static SecretKeySpec generateMySQLAESKey(final String key, final String encoding) throws Exception{
        final byte[] finalKey = new byte[16];
        int i = 0;
        for(byte b : key.getBytes(encoding))
            finalKey[i++%16] ^= b;
        return new SecretKeySpec(finalKey, "AES");
}
 
 
public static void main(String[] args) throws Exception {
    System.out.println(AesDecrypt("aIEa91QRlYjbsbGCdPHHWw==","abcdefgh12345678"));
}


6.错误码




错误码

错误信息

10005000

未知服务器错误

10005001

mock数据出错

10005050

入参校验错误:

10005051

日期错误:

10004001

未知的自定义函数

10004000

RPC SQL查询报错

10003015

缓存反序列化出错

10003014

缓存序列化出错

10003013

SQL模板为空

10003012

请求地址错误

10003011

签名错误

10003010

请不要使用重复的签名

10003009

timestamp值已过期

10003008

timestamp字段类型错误，需为当前毫秒级时间戳

10003007

分页参数必须配对

10003006

API不存在

10003005

请求内容不是标准的JSON格式

10003004

请求JSON不能为空

10003003

接口鉴权失败

10003002

加密算法出错

10003001

secretId有误

10003000

请求头缺失secretId、timestamp或者sign

200

请求成功

